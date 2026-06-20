"""Custom exception hierarchy for stage advancement validation.

Every exception raised by ``_validate_stage_advancement`` is a
subclass of ``StageAdvancementError`` so that call sites can catch
the base type for generic handling or the specific type for
differentiated behaviour.
"""


class StageAdvancementError(Exception):
    """Base for all stage advancement validation rejections."""


class PipelineNotRunningError(StageAdvancementError):
    """Pipeline.status is not ``'running'``."""


class StageNotFoundError(StageAdvancementError):
    """Stage row is ``None`` — no rows exist yet (race window)."""


class StageAlreadyTerminalError(StageAdvancementError):
    """Stage.status is already ``'completed'`` or ``'failed'``."""


class StageNotInOrderError(StageAdvancementError):
    """Stage name is absent from ``STAGE_ORDER``."""


class MissingInitialStageError(StageAdvancementError):
    """Expected initial stage is missing or does not match."""


class InitialStageAdvancementError(StageAdvancementError):
    """Current stage is the initial stage — advancing FROM it is illegal."""
