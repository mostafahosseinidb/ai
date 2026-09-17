# Training

The deliberate step. Nothing in ChainMind calls it, and nothing in
ChainMind depends on it.

Three jobs live here, and they answer three different questions:

| | `pretrain.py` | `train_decider.py` | `train_lora.py` |
|---|---|---|---|
| starts from | random numbers | random numbers, or a pretrained `.cmw` | somebody else's open model |
| needs | NumPy | NumPy | torch, transformers, peft |
| produces | a model that writes | a model that chooses, with a calibrated confidence | an adapter for a model trained elsewhere |
| good at | being wholly yours | being wholly yours **and** good at its job | answering hard open questions |

The middle column is the one most real work turns out to need, and it is
the one where training from scratch stops being a compromise — see below.

If what you want is the most capable assistant your hardware can run,
`train_lora.py` is the road. Both are supported, and the rest of ChainMind
cannot tell which answered — only the ledger can, because the weights
fingerprint goes into the evidence digest of every usage record.

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

## Training a decision model

```bash
python3 training/train_decider.py --dataset labelled.jsonl \
    --options refund status human other \
    --from .chainmind/models/atlas.cmw --workspace .chainmind

chainmind decide "the message to route"
```

The dataset is one JSON object per line: `{"text": ..., "label": ...}`.
`chainmind learning --export` writes prompt/response rows; a decision
dataset is labelled by you, which is the work.

### Why this is the easy direction

`pretrain.py` is honest that a model one person trains from scratch is far
worse at open-ended conversation than any open model they could run instead.
That limit comes from open-ended-ness, and it mostly goes away here. A
decision between four options over a domain you have labelled data for is a
small problem: a few thousand examples and a few million parameters is a
real model, and it is faster and cheaper than asking a large one the same
question by orders of magnitude — one forward pass, no sampling loop.

### Three splits, not two

Rows are split deterministically by hashing the text, into **train** (fits
the weights), **calibration** (fits the one temperature that turns scores
into probabilities) and **test** (measures accuracy and calibration error).
Fitting the temperature and then reporting the error on the same rows would
certify the fit against itself. The number written into the weights file is
from rows used for neither.

Hashing rather than shuffling means adding data next month does not
reshuffle what was held out, so two runs stay comparable.

### What the trainer does on your behalf

* **Keeps the best checkpoint, not the last.** On a few hundred rows the
  held-out loss bottoms out long before the training loss stops falling.
  On our own test run this changed the kept step from 599 to 30 and took
  accuracy *up*, from 89.2% to 90.4%.
* **Refuses a calibration that makes things worse.** Fitting minimises
  log-likelihood, which is not the same as minimising calibration error, and
  on a few hundred rows the two can disagree. Both are measured on the
  calibration split; if scaling loses, the scores are left alone and it says
  so.
* **Warns when the result is too good.** Perfect accuracy on a small test set
  usually means near-duplicate rows landed on both sides of the split, not
  that you have solved the problem.

### The check that matters most before deploying one

```bash
python3 training/train_decider.py ... --unfamiliar unrelated-text.txt
```

Calibration is fitted on held-out rows that *look like* the training rows.
It says nothing about an input from outside the distribution entirely. A
router trained on three kinds of support message will answer a question
about the weather, confidently, with one of its three options — because
those are the only things it can say.

`--unfamiliar` takes a file of deliberately unrelated text and reports how
often the model abstains on it. Our own number on a 0.2M-parameter router
was **50%**, which is not good enough to deploy unattended.

The fix is real and is not more calibration: add a catch-all option to
`--options` and label rows with it. A decision model can only be unsure
about what it was taught to be unsure about.

### What "zero hallucinations" does and does not mean

It is true here in the only sense that can be made precise: the output is
always a valid option, because there is no code path producing anything
except an index into the schema. That is a property of the type, not of the
model's behaviour, and `tests/test_decide.py` asserts it against a
deliberately deranged model.

It is not the same as being right, and it is not the same as knowing when it
is out of its depth. Those are the `--unfamiliar` number and the calibration
error, and both are measured rather than claimed.

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

### Deciding

* **Perfect accuracy.** Almost always the split: near-duplicate rows on both
  sides of it. Look at what is actually in the test rows.
* **Confident nonsense on unrelated input.** Expected, and measured by
  `--unfamiliar`. Add a catch-all option.
* **An option with almost no examples.** It is guessed at, not learned. The
  trainer prints the per-option counts before it starts for this reason.
* **A calibration error above about 0.1.** The confidence is not yet a
  probability. More data is the usual answer; do not raise the threshold and
  call it fixed.

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
