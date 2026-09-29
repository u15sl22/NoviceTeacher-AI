class CapabilityRegistry:
    def __init__(self): self.factories = {}
    def register(self, name, factory):
        if name in self.factories: raise ValueError('Duplicate capability: ' + name)
        self.factories[name] = factory
    def create(self, name, **dependencies):
        if name not in self.factories: raise ValueError('Unknown capability: ' + name)
        return self.factories[name](**dependencies)
