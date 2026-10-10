"""164: an economy notice waiting for its receipt while the engine stops is reported as it really went.

close() keeps the engine and the output worker running for producers. A notice delivered during the stop
is reported sent, so its briefing or indicator notice is not sent again after a restart; one whose delivery
fails, or that nothing can deliver any more, is reported unsent at once. (The notice delivered during the
stop itself: test_release_output95.test_economy_producer_waiting_for_receipt_finishes_during_shutdown.)
"""
import threading
import time

from test_release_output95 import make_host, response  # also puts Part1/program on the path
from event_host import EconomyOutputPort


def producer(host, text, result):
    thread = threading.Thread(target=lambda: result.append(EconomyOutputPort(host).send(text)))
    host.workers.append(thread)
    return thread


def tracked(engine):
    accepted = threading.Event()
    original = engine.ingress.post
    def post(*args, **kwargs):
        original(*args, **kwargs)
        accepted.set()
    engine.ingress.post = post
    return accepted


def test_a_notice_failing_during_the_stop_is_reported_unsent_without_waiting_out_the_timeout():
    sent = []
    engine, host, _ = make_host(transport=lambda data: sent.append(data) or response(400))
    host.start_io()
    accepted, result = tracked(engine), []
    producer(host, 'rejected news', result).start()
    assert accepted.wait(3)
    began = time.monotonic()
    assert host.close(timeout=5)
    assert result == [False] and len(sent) == 1 and not host.output.receipts
    assert time.monotonic() - began < 3 and host.notice_attempts == {}


def test_a_notice_nothing_can_deliver_any_more_is_reported_unsent_at_once():
    engine, host, sent = make_host()
    host.start_io()
    assert host.close(timeout=3)
    began = time.monotonic()
    assert EconomyOutputPort(host).send('after close') is False
    assert time.monotonic() - began < 1 and sent == [] and host.notice_attempts == {}


def test_without_a_stop_the_wait_ends_on_the_receipt_as_before():
    engine, host, sent = make_host()
    host.start_io()
    closed = []
    def owner():  # the engine owner thread, as in LIVE: run, then close
        host.run()
        closed.append(host.close(timeout=3))
    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert EconomyOutputPort(host).send('morning briefing') is True
        assert len(sent) == 1 and host.notice_attempts == {}
    finally:
        host.stop.set()
        thread.join(5)
    assert closed == [True]
