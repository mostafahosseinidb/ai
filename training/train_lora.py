#!/usr/bin/env python3
"""Train a LoRA adapter from feedback the system collected.

This is the deliberate step.  Nothing calls it automatically, and that is
the design: unattended training degrades a model quietly and you find out
weeks later, by which point you cannot tell which run did it.

The loop it belongs to is four steps, and skipping the last one makes the
first three pointless:

    1.  chainmind learning --export dataset.jsonl
    2.  chainmind eval --save before.json
    3.  training/train_lora.py --dataset dataset.jsonl --out adapters/v1
    4.  chainmind eval --compare before.json     # did it actually help?

Only the rows that went into *training* are excluded from evaluation --
``chainmind eval`` splits the same dataset the same deterministic way this
script does, so the two never disagree about which rows are held out.

Requirements live in training/requirements.txt and are deliberately not part
of the project's dependencies: a node running the finished adapter needs
none of them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The evaluation half lives in the package, and its splitter is the one that
# decides which rows are held out. Importing it here is what keeps training
# and evaluation honest about each other.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chainmind.evaluate import load_dataset, split_dataset  # noqa: E402


def require_dependencies():
    """Fail with instructions rather than a traceback."""
    missing = []
    for module in ("torch", "transformers", "peft", "datasets"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        raise SystemExit(
            f"missing: {', '.join(missing)}\n"
            "these are training-only; install them into their own environment:\n"
            "    python3 -m venv training/.venv\n"
            "    training/.venv/bin/pip install -r training/requirements.txt"
        )


def as_chat_text(row, tokenizer) -> str:
    """Render one row the way the base model expects a conversation."""
    messages = row["messages"]
    if getattr(tokenizer, "chat_template", None):
        return tokenizer.apply_chat_template(messages, tokenize=False)
    # A base model without a chat template still needs consistent framing.
    parts = [f"{message['role']}: {message['content']}" for message in messages]
    return "\n".join(parts) + (tokenizer.eos_token or "")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dataset", required=True,
                        help="JSONL from `chainmind learning --export`")
    parser.add_argument("--base", required=True,
                        help="the base model to adapt, e.g. Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--out", default="adapters/v1", help="where to write the adapter")
    parser.add_argument("--validation-fraction", type=float, default=0.2,
                        help="must match what `chainmind eval` uses")
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--alpha", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument("--min-rows", type=int, default=50,
                        help="refuse to train on less than this; too little data "
                             "makes a model worse, not better")
    parser.add_argument("--force", action="store_true",
                        help="train anyway on fewer rows than --min-rows")
    args = parser.parse_args(argv)

    rows = load_dataset(args.dataset)
    if not rows:
        raise SystemExit(f"{args.dataset} has no usable rows")

    train_rows, held_out = split_dataset(
        rows, validation_fraction=args.validation_fraction
    )
    print(f"dataset     {len(rows)} rows")
    print(f"training    {len(train_rows)}")
    print(f"held out    {len(held_out)}  (never seen here; `chainmind eval` scores these)")

    if not held_out:
        # Training without a held-out set means step 4 cannot happen, and step
        # 4 is the only one that tells you whether any of this worked.
        print(
            "\nWARNING: the split left nothing held out, so `chainmind eval` will "
            "have nothing\n         to score this against. Collect more feedback "
            "before trusting the result.",
            file=sys.stderr,
        )

    if len(train_rows) < args.min_rows and not args.force:
        raise SystemExit(
            f"only {len(train_rows)} training rows. Below roughly {args.min_rows} a "
            "LoRA tends to memorise rather than learn, and the result is worse than "
            "the base model. Collect more feedback, or pass --force if you know why "
            "you want this."
        )

    require_dependencies()

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForLanguageModeling,
        Trainer,
        TrainingArguments,
    )

    tokenizer = AutoTokenizer.from_pretrained(args.base)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    texts = [as_chat_text(row, tokenizer) for row in train_rows]
    dataset = Dataset.from_dict({"text": texts}).map(
        lambda batch: tokenizer(batch["text"], truncation=True,
                                max_length=args.max_length),
        batched=True, remove_columns=["text"],
    )

    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
        device_map="auto" if torch.cuda.is_available() else None,
    )
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=args.alpha, lora_dropout=0.05,
        bias="none", task_type="CAUSAL_LM",
    ))
    model.print_trainable_parameters()

    output = Path(args.out)
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(output / "checkpoints"),
            num_train_epochs=args.epochs,
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            learning_rate=args.learning_rate,
            logging_steps=5,
            save_strategy="no",
            bf16=torch.cuda.is_available(),
            report_to=[],
        ),
        train_dataset=dataset,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )
    trainer.train()

    output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(output)
    tokenizer.save_pretrained(output)

    # The provenance of an adapter matters as much as the adapter: which rows,
    # which base, which settings. Without it, "v3 is better" is unrepeatable.
    (output / "provenance.json").write_text(json.dumps({
        "base": args.base,
        "dataset": str(Path(args.dataset).resolve()),
        "rows_total": len(rows),
        "rows_trained": len(train_rows),
        "rows_held_out": len(held_out),
        "held_out_keys": [row.get("digest") for row in held_out if row.get("digest")],
        "validation_fraction": args.validation_fraction,
        "epochs": args.epochs, "rank": args.rank, "alpha": args.alpha,
        "learning_rate": args.learning_rate,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nadapter     {output}")
    print("\nnext, and do not skip it:")
    print("    chainmind eval --compare before.json")
    print("if the delta is not positive, this adapter is not an improvement.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
