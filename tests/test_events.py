import asyncio

from app.events import EventBus


def test_event_bus_register_publish_unregister():
    async def scenario():
        bus = EventBus()
        queue = bus.register()
        bus.publish({"type": "command"})
        assert await asyncio.wait_for(queue.get(), 0.1) == {"type": "command"}
        bus.unregister(queue)
        assert not bus._subscribers

    asyncio.run(scenario())
