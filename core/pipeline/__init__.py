"""The staged main pipeline: context, stages, and the runner."""

from core.pipeline.context import PipelineContext, PipelineDeps
from core.pipeline.runner import PipelineRunner, default_stages

__all__ = ["PipelineContext", "PipelineDeps", "PipelineRunner", "default_stages"]
