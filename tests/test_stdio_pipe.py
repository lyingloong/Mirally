"""Real Windows pipe framing and EOF checks; no model or desktop access."""
import io
import os
import sys
import threading
import time
import unittest

from openassist.runtime.ufo.worker import WorkerChannel, stdio_channel


@unittest.skipUnless(sys.platform == "win32", "Windows inherited pipe")
class ControlPipeTests(unittest.TestCase):
    def setUp(self):
        from openassist.platform.stdio_pipe import control_pipe_reader
        descriptor, self.writer = os.pipe()
        self.original = io.FileIO(descriptor, "rb")
        self.reader = control_pipe_reader(self.original)
        self.addCleanup(self.original.close)
        self.addCleanup(self.reader.close)
        self.addCleanup(self.close_writer)

    def close_writer(self):
        if self.writer is not None:
            os.close(self.writer)
            self.writer = None

    def test_utf8_split_frame_crlf_and_following_frame(self):
        lines = []
        done = threading.Event()

        def read():
            lines.extend((self.reader.readline(), self.reader.readline()))
            done.set()

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        payload = "中文😀\r\nnext\n".encode("utf-8")
        os.write(self.writer, payload[:1])
        time.sleep(0.03)  # Decoder must wait for the rest of the UTF-8 character.
        os.write(self.writer, payload[1:])
        self.assertTrue(done.wait(2))
        thread.join(1)
        self.assertEqual(lines, ["中文😀\n", "next\n"])

    def test_line_limit_and_eof_preserve_text_stream_contract(self):
        os.write(self.writer, b"123456789\ntail")
        self.close_writer()
        self.assertEqual(self.reader.readline(5), "12345")
        self.assertEqual(self.reader.readline(), "6789\n")
        self.assertEqual(self.reader.readline(), "tail")
        self.assertEqual(self.reader.readline(), "")
        self.reader.close()
        os.fstat(self.original.fileno())  # Borrowed stdin handle is still owned by caller.

    def test_product_channel_receives_cancel_and_eof_revokes_work(self):
        channel = stdio_channel(self.original, io.StringIO())
        self.addCleanup(channel.source.close)
        self.addCleanup(self.close_writer)  # Release an idle reader before closing its wrapper.
        thread = threading.Thread(target=channel.listen, daemon=True)
        thread.start()
        os.write(self.writer, b'{"type":"cancel"}\n')
        self.assertTrue(channel.ready.wait(2))
        self.assertTrue(channel.stopped)
        self.close_writer()
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_eof_without_message_revokes_work(self):
        channel = WorkerChannel(self.reader, io.StringIO())
        thread = threading.Thread(target=channel.listen, daemon=True)
        thread.start()
        self.close_writer()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(channel.ready.is_set())
        self.assertTrue(channel.stopped)


if __name__ == "__main__":
    unittest.main()
