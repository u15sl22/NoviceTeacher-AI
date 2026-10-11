from .context import ContextPipeline, ContextAssembler


class PipelineContextEngine:
    def __init__(self, contributors, max_bytes):
        self.pipeline = ContextPipeline(contributors)
        self.assembler = ContextAssembler(max_bytes)
    def prepare(self, request, audit, system_prompt=None):
        fragments = self.pipeline.build(request)
        for fragment in fragments:
            record = fragment.metadata.pop('retrieval_record', None)
            if record is not None: audit.retrieved(record)
        return self.assembler.assemble(fragments, system_prompt) if system_prompt else self.assembler.assemble(fragments)


class LegacyContextEngine:
    """Frozen v1 context shape retained for pre-alpha sessions."""
    def __init__(self, memory, builder): self.memory, self.builder = memory, builder
    def prepare(self, request, audit, system_prompt=None):
        audit.retrieved({'items': []})
        context = self.builder.build(request.section, request.session['lesson_metadata'],
            self.memory.build_memory(request.session, request.round, request.section), [],
            request.refinement.get('prompt_text') if request.refinement else None)
        return context.model_copy(update={'system': system_prompt}) if system_prompt else context
