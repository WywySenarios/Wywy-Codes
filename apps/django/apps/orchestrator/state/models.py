"""Dataclass models matching the state.json schema.

Valid status values:
  pending | queued | running | blocked | completed | failed | cancelled
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

# All valid status values for pipeline and stage states.
VALID_STATUSES: frozenset[str] = frozenset({
    "pending",
    "queued",
    "running",
    "blocked",
    "completed",
    "failed",
    "cancelled",
})

# Terminal statuses — no further transitions allowed from these.
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

# Ordered list of pipeline stages in execution order.
STAGE_NAMES: tuple[str, ...] = (
    "init",
    "RED",
    "GREEN",
    "REFRACTOR",
    "compilance",
    "PR writer",
)


@dataclass
class StageState:
    """State for a single pipeline stage."""

    status: str = "pending"
    output: Optional[dict] = None
    retry_count: int = 0


@dataclass
class Artifacts:
    """Paths to pipeline artifact files on the shared workspace."""

    plan: str = "artifacts/plan.md"
    spec: str = "artifacts/spec.md"
    tests: str = "artifacts/tests/"
    integration_tests: str = "artifacts/integration_tests/"
    e2e_tests: str = "artifacts/e2e_tests/"
    pr_payload: str = "artifacts/pr_payload.json"


@dataclass
class ErrorEntry:
    """A single error record."""

    stage: str
    message: str
    timestamp: str = ""  # ISO 8601

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


@dataclass
class PipelineState:
    """Full pipeline state matching the state.json schema."""

    pipeline_id: str = ""
    invocation_name: str = ""
    status: str = "pending"
    current_stage: str = ""
    iteration_count: int = 0
    user_input_pending: bool = False
    user_input_prompt: Optional[str] = None
    artifacts: Artifacts = field(default_factory=Artifacts)
    stages: dict[str, StageState] = field(default_factory=dict)
    errors: list[ErrorEntry] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.pipeline_id:
            self.pipeline_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if not self.stages:
            self.stages = {name: StageState() for name in STAGE_NAMES}

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict matching the state.json schema."""

        # Recursively convert dataclasses, dicts, and lists to plain types.
        def _convert(obj: object) -> object:
            if isinstance(obj, (datetime,)):
                return obj.isoformat()
            if hasattr(obj, "__dataclass_fields__"):
                return {k: _convert(v) for k, v in asdict(obj).items()}
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_convert(v) for v in obj]
            return obj

        result: dict[str, object] = {}
        result["pipeline_id"] = self.pipeline_id
        result["invocation_name"] = self.invocation_name
        result["status"] = self.status
        result["current_stage"] = self.current_stage
        result["iteration_count"] = self.iteration_count
        result["user_input_pending"] = self.user_input_pending
        result["user_input_prompt"] = self.user_input_prompt
        result["artifacts"] = asdict(self.artifacts)
        result["stages"] = {
            name: asdict(stage) for name, stage in self.stages.items()
        }
        result["errors"] = [asdict(e) for e in self.errors]
        result["created_at"] = self.created_at
        result["updated_at"] = self.updated_at
        return result

    @classmethod
    def from_dict(cls, data: object) -> PipelineState:
        """Deserialise from a plain dict (e.g. parsed JSON)."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")
        stages_raw: dict = data.get("stages", {})
        stages: dict[str, StageState] = {}
        for name in STAGE_NAMES:
            raw = stages_raw.get(name, {})
            stages[name] = StageState(
                status=raw.get("status", "pending"),
                output=raw.get("output"),
                retry_count=raw.get("retry_count", 0),
            )

        errors_raw: list = data.get("errors", [])
        errors: list[ErrorEntry] = [
            ErrorEntry(
                stage=e.get("stage", ""),
                message=e.get("message", ""),
                timestamp=e.get("timestamp", ""),
            )
            for e in errors_raw
        ]

        artifacts_raw: dict = data.get("artifacts", {})
        artifacts = Artifacts(
            plan=artifacts_raw.get("plan", "artifacts/plan.md"),
            spec=artifacts_raw.get("spec", "artifacts/spec.md"),
            tests=artifacts_raw.get("tests", "artifacts/tests/"),
            integration_tests=artifacts_raw.get(
                "integration_tests", "artifacts/integration_tests/"
            ),
            e2e_tests=artifacts_raw.get("e2e_tests", "artifacts/e2e_tests/"),
            pr_payload=artifacts_raw.get(
                "pr_payload", "artifacts/pr_payload.json"
            ),
        )

        return cls(
            pipeline_id=data.get("pipeline_id", ""),
            invocation_name=data.get("invocation_name", ""),
            status=data.get("status", "pending"),
            current_stage=data.get("current_stage", ""),
            iteration_count=data.get("iteration_count", 0),
            user_input_pending=data.get("user_input_pending", False),
            user_input_prompt=data.get("user_input_prompt"),
            artifacts=artifacts,
            stages=stages,
            errors=errors,
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )
