"""The public package stands alone: its eight authoring modules agree."""

import asyncio

from skulk_capability_sdk import (
    PROTOCOL,
    contracts,
    credentials,
    database,
    operations,
    serving,
    storage,
    streams,
    wire,
)


def test_the_eight_modules_import_without_the_private_sdk() -> None:
    for module in (
        contracts,
        wire,
        storage,
        operations,
        database,
        serving,
        streams,
        credentials,
    ):
        assert not module.__name__.startswith("skulk_capability_nodes")
    assert PROTOCOL == 4


def test_a_frame_round_trips_through_the_wire() -> None:
    """A real stream pair, so the public reader and writer contracts are typed."""
    payload = contracts.Shutdown()
    assert payload.protocol == PROTOCOL

    async def run() -> contracts.WireMessage:
        received: asyncio.Future[contracts.WireMessage] = (
            asyncio.get_running_loop().create_future()
        )

        async def serve(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                received.set_result(await wire.read_message(reader))
            finally:
                writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            await wire.write_message(writer, payload)
            writer.close()
            await writer.wait_closed()
            return await asyncio.wait_for(received, 5)

    assert asyncio.run(run()) == payload
