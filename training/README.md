# Training

The deliberate step. Nothing in ChainMind calls it, and nothing in
ChainMind depends on it.

## Why it is separate

A node runs a finished adapter with no training stack installed. Keeping
`torch` out of the project's dependencies is the difference between "install
and become a node" being true on a small VPS and being true only on a
workstation. So this directory has its own environment:

```bash
python3 -m venv training/.venv
training/.venv/bin/pip install -r training/requirements.txt
```

Pick the torch build matching your hardware first; see pytorch.org.

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

For Ollama, write a Modelfile pointing at the base and the adapter:

```
FROM qwen2.5:7b
ADAPTER ./adapters/v1
```

```bash
ollama create atlas-v1 -f Modelfile
chainmind eval --model atlas-v1 --compare before.json
```

For llama.cpp, convert the adapter to GGUF with its `convert_lora_to_gguf.py`
and pass it with `--lora`.

## What will go wrong

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
