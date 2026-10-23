"""`/omh-model` no longer stops the gateway (parity P14): it is read while the picker is open.

The picker runs on the alternate screen and reads its keys through the app
(`App._picker_reader`). Between reads the app takes what the gateway sent:
a streaming reply goes on into the transcript underneath, and a server
request closes the picker the way it closes the pager, with nothing saved;
the question is open once the picker's screen is gone. Every case drives an
`App` over the recording client; the terminal's reads are scripted on the
app's clock.
"""

from __future__ import annotations

import unittest

from test_tui_modes import SID, make_app, notices
from omh.commands.theme_picker import KEY_DOWN, KEY_QUIT
from omh.tui.app import CHAINS_CLOSED_NOTICE, READ_TIMEOUT_SECONDS
from omh.tui.gateway import Event, GatewayExited, ServerRequest
from omh.tui.transcript import AssistantBlock


READS_BEFORE_A_KEY = 50  # a picker key never takes this many empty reads here: past it the reader is stuck


def scripted(app, reads: list[bytes]) -> list[int]:
    """Script the terminal's reads; the returned list holds how many reads were made so far.

    Past `READS_BEFORE_A_KEY` a read fails the test. It never raises
    EOFError there: the picker's reader turns that into the cancel key, and a
    reader that missed its cue would pass as one that cancelled.
    """
    now = [100.0]
    app.clock = lambda: now[0]
    count = [0]

    def read(timeout: float, wake: int | None = None) -> bytes:
        now[0] += READ_TIMEOUT_SECONDS
        count[0] += 1
        if count[0] > READS_BEFORE_A_KEY:
            raise AssertionError("the picker's reader kept reading")
        return reads.pop(0) if reads else b""

    app.terminal.read = read  # type: ignore[method-assign]
    return count


class DrainWhileOpenTests(unittest.TestCase):
    def test_a_streaming_reply_goes_on_into_the_transcript_while_the_picker_is_open(self) -> None:
        keys: list[str] = []
        app, client = make_app(busy=True)

        def pick(environ: object, theme: object, *, read_key, write, width: int) -> str:
            client.inbox.extend([Event("message.start", SID, {}), Event("message.delta", SID, {"text": "still streaming"})])
            keys.append(read_key())
            return "No model-chain changes."

        app.pick_model_chains = pick
        scripted(app, [b"", b"\x1b[B"])
        app._omh_model("")
        self.assertEqual(keys, [KEY_DOWN])
        blocks = [block for block in app.transcript.blocks if isinstance(block, AssistantBlock)]
        self.assertEqual([block.text for block in blocks], ["still streaming"])
        self.assertEqual(notices(app)[-1], "No model-chain changes.")

    def test_a_question_closes_the_picker_unsaved_and_opens_once_it_is_gone(self) -> None:
        keys: list[str] = []
        app, client = make_app(busy=True)

        def pick(environ: object, theme: object, *, read_key, write, width: int) -> str:
            client.inbox.append(ServerRequest("srq-1", "approval", {"command": "rm -rf ./x"}))
            for _ in range(5):  # bounded: a reader that hands back other keys stops here instead of looping
                keys.append(read_key())
                if keys[-1] == KEY_QUIT:
                    break
            return "Cancelled; model-chains.json was not changed."

        app.pick_model_chains = pick
        reads = scripted(app, [])
        app._omh_model("")
        self.assertEqual(keys, [KEY_QUIT])
        self.assertEqual(reads, [1])  # the read that took the request in; the cancel came next, not a read later
        self.assertIsNotNone(app.slot)
        self.assertEqual(client.answers, [])  # closing the picker answers nothing
        self.assertEqual(notices(app)[-1], f"{CHAINS_CLOSED_NOTICE} Cancelled; model-chains.json was not changed.")
        self.assertFalse(app.chains_open or app.chains_closing)

    def test_the_gateway_leaving_cancels_it_too(self) -> None:
        keys: list[str] = []
        app, client = make_app()

        def pick(environ: object, theme: object, *, read_key, write, width: int) -> str:
            client.inbox.append(GatewayExited(1))
            keys.append(read_key())
            return "Cancelled; nothing was changed."

        app.pick_model_chains = pick
        scripted(app, [])
        app._omh_model("")
        self.assertEqual(keys, [KEY_QUIT])
        self.assertEqual(app.exit_code, 1)

    def test_a_question_after_the_picker_closed_opens_as_ever(self) -> None:
        app, client = make_app()
        app.pick_model_chains = lambda environ, theme, *, read_key, write, width: "No model-chain changes."
        scripted(app, [])
        app._omh_model("")
        app._on_server_request(ServerRequest("srq-2", "approval", {"command": "ls"}))
        self.assertIsNotNone(app.slot)
        self.assertNotIn(CHAINS_CLOSED_NOTICE, " ".join(notices(app)))


if __name__ == "__main__":
    unittest.main()
