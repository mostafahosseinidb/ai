"""ChainMind -- an autonomous agent whose resource use is governed by a ledger.

The short version of the idea: an agent here cannot consume compute, tokens,
storage, bandwidth or external calls unless the chain has granted it the
credits and left room in its quota.  Every unit it does consume becomes a
signed, hash-linked, replayable record.  Autonomy and accountability are the
same mechanism rather than opposing ones.

Typical use::

    from chainmind import AgentKernel, Chain, GenesisConfig, LocalLedger, SigningKey

    authority = SigningKey.generate()
    chain = Chain.create(GenesisConfig(authorities={authority.public_hex(): 10_000_000}),
                         authority)
    agent = AgentKernel(SigningKey.generate(), LocalLedger(chain, authority), label="atlas")
    agent.run("summarise today's telemetry")
"""

from .agent import ActionOutcome, AgentHalted, AgentKernel, BudgetAwarePlanner, LocalLedger, Step, StaticPlanner
from .block import Block, BlockHeader
from .chain import Chain, ChainError, LedgerBusy, ProposalResult
from .chat import ChatBusy, ChatRejected, ChatService, Conversation
from .consensus import ConsensusError, ProofOfAuthority, ProofOfWork
from .crypto import SigningKey, VerifyingKey, merkle_root, sha256_hex
from .local import LocalModel, LocalRuntimeUnavailable, discover_runtime
from .embedded import EmbeddedModel, EmbeddedUnavailable, discover_models, model_fingerprint
from .nano import Architecture, NanoModel, WeightsError, load_weights, save_weights
from .native import NativeModel, NativeUnavailable, discover_native
from .decide import (
    Calibration,
    Decision,
    DecisionModel,
    DecisionSchema,
    DecisionUnavailable,
    discover_deciders,
    expected_calibration_error,
    fit_temperature,
)
from .tokenizer import Tokenizer, train_tokenizer
from .evaluate import EvalReport, Evaluation, RowResult, rouge_l, split_dataset, token_f1
from .executors import Executor, InProcessExecutor, SandboxExecutor
from .memory import Feedback, FeedbackStore, Hit, MemoryStore, Note, build_training_dataset
from .mempool import Mempool
from .p2p import Node, NodeError, PeerInfo, join_network, submit_to_node
from .meter import Meter, UsageRecord
from .sandbox import SUPPORTED as SANDBOX_SUPPORTED, SandboxLimits, SandboxResult, SandboxUnavailable
from .resources import CREDIT, DEFAULT_PRICES, MICROCREDIT, PriceTable, ResourceKind, format_credits
from .state import Account, GenesisConfig, Receipt, StateError, WorldState
from .tools import Tool, ToolError, ToolRegistry, ToolResult, default_registry
from .transactions import (
    InvalidTransaction,
    Transaction,
    TxType,
    build_attestation,
    build_grant,
    build_policy_update,
    build_registration,
    build_usage,
)

__version__ = "0.10.0"

__all__ = [
    "ActionOutcome", "Account", "AgentHalted", "AgentKernel", "Block", "BlockHeader",
    "ChatBusy", "ChatRejected", "ChatService", "Conversation", "LedgerBusy",
    "LocalModel", "LocalRuntimeUnavailable", "discover_runtime",
    "EmbeddedModel", "EmbeddedUnavailable", "discover_models", "model_fingerprint",
    "Architecture", "NanoModel", "WeightsError", "load_weights", "save_weights",
    "NativeModel", "NativeUnavailable", "discover_native",
    "Calibration", "Decision", "DecisionModel", "DecisionSchema",
    "DecisionUnavailable", "discover_deciders", "expected_calibration_error",
    "fit_temperature",
    "Tokenizer", "train_tokenizer",
    "Node", "NodeError", "PeerInfo", "join_network", "submit_to_node",
    "Feedback", "FeedbackStore", "Hit", "MemoryStore", "Note", "build_training_dataset",
    "EvalReport", "Evaluation", "RowResult", "rouge_l", "split_dataset", "token_f1",
    "Executor", "InProcessExecutor", "SandboxExecutor", "SandboxLimits", "SandboxResult",
    "SandboxUnavailable", "SANDBOX_SUPPORTED",
    "BudgetAwarePlanner", "CREDIT", "Chain", "ChainError", "ConsensusError", "DEFAULT_PRICES",
    "GenesisConfig", "InvalidTransaction", "LocalLedger", "MICROCREDIT", "Mempool", "Meter",
    "PriceTable", "ProofOfAuthority", "ProofOfWork", "ProposalResult", "Receipt", "ResourceKind",
    "SigningKey", "StateError", "Step", "StaticPlanner", "Tool", "ToolError", "ToolRegistry",
    "ToolResult", "Transaction", "TxType", "UsageRecord", "VerifyingKey", "WorldState",
    "build_attestation", "build_grant", "build_policy_update", "build_registration",
    "build_usage", "default_registry", "format_credits", "merkle_root", "sha256_hex",
    "__version__",
]
