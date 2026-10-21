"""Minimal internal plugin example; runtime owns this object and its handle."""
class ExamplePlugin:
    def __init__(self, service):
        self.service = service
        self.handle = None
        self.received = []

    async def enable(self):
        self.handle = await self.service.plugin_enabled('example')
        self.service.bus.register(self.handle, 'example.tick', {
            'type': 'object', 'properties': {'value': {'type': 'integer'}},
            'required': ['value'], 'additionalProperties': False})
        async def receive(event):
            # Real plugins process notifications here without blocking the loop.
            self.received.append(event['id'])
            del self.received[:-100]
        self.service.bus.subscribe(self.handle, 'example.observer', receive)
        await self.service.plugin_ready()

    def tick(self, value):
        return self.service.bus.publish(self.handle, 'example.tick', {'value': value})

    async def disable(self):
        await self.service.plugin_disabled(self.handle)
        self.handle = None
