"""
Pydantic schema contracts for the Lumen knowledge graph and pipeline.

See: docs/Graph/Schema.md, docs/hld/Technical_HLD.md Section 5,
     implementation/Goal_2_Plan.md
"""

from lumen.schemas.base import (
    GraphNode,
    LumenNode,
    PersonRefsMixin,
    SignalProvenanceMixin,
    TemporalNode,
    VersionedNode,
    model_to_graph_dict,
)
from lumen.schemas.edges import (
    DialecticEdge,
    EvolvedFromEdge,
    LogicalEdgeType,
    LumenEdge,
    ReconciliationEdge,
    RegulatesEdge,
    UnsupportedEdgeError,
    resolve_edge_table,
)
from lumen.schemas.ids import (
    NODE_ID_PREFIXES,
    SEMANTIC_ID_RE,
    make_node_id,
    make_slug_node_id,
)
from lumen.schemas.nodes import (
    AdoptedPrincipleNode,
    BeliefNode,
    CausalChainNode,
    CausalStepNode,
    ContradictionNode,
    DecisionAuditNode,
    EpisodeNode,
    EventNode,
    LessonNode,
    LifecycleHistoryEntry,
    MacroextractionReportNode,
    ObservationNode,
    OpenLoopNode,
    PatternNode,
    PersonEntityNode,
    RollbackPointer,
    SessionNode,
)
from lumen.schemas.pipeline import (
    AmbiguousRef,
    BufferMessage,
    CandidateNode,
    CoreferenceMap,
    ExtractionResult,
    PipelineDTO,
    PreprocessedEpisode,
    PreprocessingResult,
    ReconciliationResult,
    ResolvedEntity,
    RetrievalResult,
    SessionDecayEvent,
)

__all__ = [
    # base
    "GraphNode",
    "LumenNode",
    "TemporalNode",
    "VersionedNode",
    "SignalProvenanceMixin",
    "PersonRefsMixin",
    "model_to_graph_dict",
    # ids
    "NODE_ID_PREFIXES",
    "SEMANTIC_ID_RE",
    "make_node_id",
    "make_slug_node_id",
    # nodes
    "EpisodeNode",
    "ObservationNode",
    "EventNode",
    "SessionNode",
    "CausalChainNode",
    "CausalStepNode",
    "PatternNode",
    "BeliefNode",
    "LessonNode",
    "AdoptedPrincipleNode",
    "PersonEntityNode",
    "DecisionAuditNode",
    "ContradictionNode",
    "MacroextractionReportNode",
    "OpenLoopNode",
    "LifecycleHistoryEntry",
    "RollbackPointer",
    # edges
    "LogicalEdgeType",
    "LumenEdge",
    "ReconciliationEdge",
    "EvolvedFromEdge",
    "DialecticEdge",
    "RegulatesEdge",
    "resolve_edge_table",
    "UnsupportedEdgeError",
    # pipeline
    "PipelineDTO",
    "BufferMessage",
    "ResolvedEntity",
    "AmbiguousRef",
    "CoreferenceMap",
    "PreprocessedEpisode",
    "CandidateNode",
    "SessionDecayEvent",
    "PreprocessingResult",
    "ExtractionResult",
    "RetrievalResult",
    "ReconciliationResult",
]
