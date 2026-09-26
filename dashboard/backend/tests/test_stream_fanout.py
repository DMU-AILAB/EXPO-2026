import asyncio

import pytest

from app.services.stream_fanout import MjpegStreamFanout, _JpegFrameParser


def test_jpeg_parser_handles_multipart_chunks_split_at_any_byte():
    parser = _JpegFrameParser()
    jpeg = b"\xff\xd8fake-jpeg\xff\xd9"
    payload = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg

    frames = []
    for index in range(0, len(payload), 3):
        frames.extend(parser.feed(payload[index:index + 3]))

    assert frames == [jpeg]


@pytest.mark.asyncio
async def test_viewers_share_one_channel_and_receive_latest_frame(monkeypatch):
    manager = MjpegStreamFanout()

    async def fake_run(channel):
        while channel.subscribers:
            await asyncio.sleep(0.001)

    monkeypatch.setattr(manager, "_run", fake_run)

    channel_one, queue_one = await manager.subscribe("http://pi/stream.mjpg")
    channel_two, queue_two = await manager.subscribe("http://pi/stream.mjpg")
    assert channel_one is channel_two

    packet = manager._frame_packet(b"\xff\xd8frame\xff\xd9")
    manager._publish(channel_one, packet)
    assert await queue_one.get() == packet
    assert await queue_two.get() == packet

    await manager.close()

