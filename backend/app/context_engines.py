from .context import ContextPipeline, ContextAssembler


class PipelineContextEngine:
    def __init__(self, contributors, max_tokens):
        self.pipeline = ContextPipeline(contributors)
        self.assembler = ContextAssembler(max_tokens)
    def prepare(self, request, audit):
        fragments = self.pipeline.build(request)
        for fragment in fragments:
            record = fragment.metadata.pop('retrieval_record', None)
            if record is not None: audit.retrieved(record)
        return self.assembler.assemble(fragments)


class LegacyContextEngine:
    """Frozen v1 context shape retained for pre-alpha sessions."""
    def __init__(self, memory, builder): self.memory, self.builder = memory, builder
    def prepare(self, request, audit):
        audit.retrieved({'items': []})
        return self.builder.build(request.section, request.session['lesson_metadata'],
            self.memory.build_memory(request.session, request.round, request.section), [])
