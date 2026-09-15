# Training

The deliberate step. Nothing in ChainMind calls it, and nothing in
ChainMind depends on it.

Two different jobs live here, and they answer two different questions:

| | `pretrain.py` | `train_lora.py` |
|---|---|---|
| starts from | random numbers | somebody else's open model |
| needs | NumPy | torch, transformers, peft |
| produces | a `.cmw` file — architecture, vocabulary and weights all from this project | an adapter for a model trained elsewhere |
| good at | being wholly yours | actually answering hard questions |

If what you want is a system with **no outside parts**, `pretrain.py` is the
one. If what you want is the most capable assistant your hardware can run,
`train_lora.py` is. Both are supported, and the rest of ChainMind cannot
tell which answered — only the ledger can, because the weights fingerprint
goes into the evidence digest of every usage record.

## Why this is separate from the package

A node runs a finished model with no training stack installed. Keeping
`torch` — and even NumPy — out of the project's dependencies is the
difference between "install and become a node" being true on a small VPS and
being true only on a workstation. So this directory has its own environment:

```bash
python3 -m venv training/.venv
training/.venv/bin/pip install -r training/requirements.txt
```

`pretrain.py` needs only the first line of that file. For `train_lora.py`,
pick the torch build matching your hardware first; see pytorch.org.

## Training the project's own model

```bash
pip install numpy
mkdir -p corpus && cp <your text> corpus/

python3 training/pretrain.py --corpus corpus --workspace .chainmind \
    --dim 256 --layers 6 --steps 4000

python3 -m chainmind.cli runtime          # should now report your own model
```

It learns a byte-level BPE vocabulary from *your* corpus (a borrowed
vocabulary spends three or four tokens on a Persian word that deserves one,
and no amount of training buys those tokens back), initialises a
decoder-only transformer from noise, and trains it. The forward and backward
passes are written out in that file rather than delegated to a framework, so
the only dependency is an array library and the arithmetic is readable next
to the inference code it has to match. Every gradient is checked against a
finite difference in `tests/test_pretrain.py`, because a transformer with
one wrong sign trains to a plausible-looking loss and produces nothing.

### What to expect, stated plainly

A model one person can train from scratch is small. Useful models need
roughly **twenty tokens of training data per parameter**: ten million
parameters wants two hundred million tokens, which is more Persian text than
most people have. Below that ratio the model memorises rather than
generalises.

So it will write text in the shape and register of your corpus, answer in
the style of your own recorded conversations, and be **far worse at general
knowledge and reasoning than any open model you could have run instead**.
That is the arithmetic of pretraining, not a defect in this code.

### Making it answer rather than continue

A corpus of prose teaches continuation. To get a model that answers
questions, its corpus has to contain conversations:

```bash
chainmind learning --export corpus/chats.jsonl
python3 training/pretrain.py --corpus corpus --workspace .chainmind
```

`pretrain.py` reads those rows, keeps their roles as control tokens, and
records in the weights file that it saw them. `chainmind.native` reads that
back: a model trained without conversations is given plain text instead of
a chat template, because feeding control tokens to a model that has never
seen them turns the output into noise and looks like a broken model rather
than a corpus with no conversations in it.

## Fine-tuning open weights instead

The loop below adapts somebody else's model. It is the faster road to a
capable assistant and the slower road to independence.

## The loop

Four steps. The fourth is the one people skip, and skipping it makes the
other three pointless.

```bash
# 1. what the system collected
chainmind learning --export dataset.jsonl

# 2. how good it is now
chainmind eval --save before.json

# 3. train
training/.venv/bin/python training/train_lora.py \
    --dataset dataset.jsonl \
    --base Qwen/Qwen2.5-7B-Instruct \
    --out adapters/v1

# 4. did it actually help?
chainmind eval --compare before.json
```

Training and evaluation split the dataset **the same deterministic way**,
so the rows scored in step 4 are rows step 3 never saw. That is not a
convention to remember: `train_lora.py` imports the splitter from
`chainmind.evaluate` rather than reimplementing it, so the two cannot drift
apart.

If the delta in step 4 is not positive, the adapter is not an improvement.
Keep the base model.

## Serving the adapter

Merge the adapter into the base and convert the result to GGUF with
llama.cpp's `convert_hf_to_gguf.py`, then drop the file in
`.chainmind/models/` — the embedded backend loads it in this process with
nothing else to run:

```bash
chainmind runtime
chainmind eval --model .chainmind/models/atlas-v1.gguf --compare before.json
```

If you already run an inference server here, any of them will serve the
adapter too; point `CHAINMIND_LOCAL_URL` at it. That path works and is not
the default, because it is a second program to install and keep running.

## What will go wrong

### Pretraining

* **Loss stuck near ln(vocab_size).** The model is still guessing
  uniformly. Almost always the learning rate: too low and it never moves,
  too high and it diverges in the first dozen steps and comes back as NaN.
  Start at `--lr 3e-3` for a small model and watch the first fifty steps.
* **Training loss far below the holdout loss.** Memorisation. Either bring
  more text or make the model smaller (`--dim`, `--layers`). The script
  prints the tokens-per-parameter ratio before it starts for this reason.
* **It continues your text instead of answering.** The corpus had no
  conversations in it. See above.
* **It is slow.** NumPy on a CPU is what this is. A few million parameters
  over a few thousand steps is an evening, not a week — but it is also not
  a minute.

### Fine-tuning

* **Too little data.** Below roughly 50 training rows a LoRA memorises
  instead of learning and the result is worse than the base model. The
  script refuses; `--force` exists for when you know why you want it.
* **Every row rated "good".** A dataset with no disagreement in it teaches
  very little. Rate the bad answers too -- `--rating bad` exports those, and
  they are the more informative half.
* **Improvement on the training rows only.** That is memorisation, which is
  exactly what step 4 is designed to catch.
* **Drift.** Each adapter is trained on a snapshot. `provenance.json` next to
  the adapter records which rows, which base and which settings, because
  "v3 is better" is worthless if it cannot be reproduced.

## What this is not

It is not continuous, and it is not automatic. Unattended training degrades
a model quietly and you find out weeks later, unable to tell which run did
it. The loop above is slow and boring on purpose.
