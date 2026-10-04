"""Real Tk checks of the native client without showing windows or moving input."""
import queue
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from openassist.native_shell import NativeShell


class Transport:
    def __init__(self):
        self.events = queue.SimpleQueue()
        self.requests = []
        self.closed = False

    def start(self):
        pass

    def request(self, op, **params):
        identifier = str(len(self.requests))
        self.requests.append((identifier, op, params))
        return identifier

    def close(self):
        self.closed = True


@unittest.skipUnless(sys.platform == 'win32', 'Windows HWND presentation')
class NativeShellTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='mirally-native-ui-')
        self.transport = Transport()
        self.shell = NativeShell(Path(self.directory.name), startup=False, visible=False, transport=self.transport)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.shell._close)
        self.shell._event({'type':'connection','connected':True})
        self.select('agent:main:one')

    def select(self, key, **kw):
        self.shell._event({'type':'session','key':key,'entries':[], 'activeRunIds':[],**kw})

    def test_experimental_entry_bypasses_ufo_bootstrap(self):
        from openassist.__main__ import main
        with patch('sys.argv',['openassist','--companion']), patch('openassist.native_shell.NativeShell') as factory:
            main()
        factory.assert_called_once()
        factory.return_value.run.assert_called_once()

    def test_native_input_does_not_request_desktop_ownership(self):
        self.select('agent:main:one', activeRunIds=['exact-run'])
        with patch.object(self.shell.session,'passthrough') as passthrough, patch.object(self.shell.session,'register_hotkeys') as hotkeys:
            self.assertTrue(self.shell._begin_composing())
            self.shell._end_composing()
            self.assertTrue(self.shell._send_text('继续执行，保留原文件'))
        passthrough.assert_not_called()
        hotkeys.assert_not_called()
        _,op,params = self.transport.requests[-1]
        self.assertEqual(op,'send')
        self.assertEqual(params['key'],'agent:main:one')
        self.assertEqual(self.shell._snapshot['activeRunIds'],['exact-run'])

    def test_stop_targets_one_exact_native_run(self):
        self.select('agent:main:one',activeRunIds=['exact-run'])
        self.shell._stop()
        self.assertEqual(self.transport.requests[-1][1:],('abort',{'key':'agent:main:one','runId':'exact-run'}))
        self.select('agent:main:one',activeRunIds=['run-one','run-two'])
        self.assertFalse(self.shell.can_stop)
        count=len(self.transport.requests)
        self.shell._stop()
        self.assertEqual(len(self.transport.requests),count)

    def test_streaming_updates_replace_the_reply_and_preserve_user_text(self):
        user={'message':{'role':'user','content':[{'type':'text','text':'你好'}]},'pending':False}
        assistant={'message':{'role':'assistant','content':[{'type':'text','text':'你好，我是'}]},'live':True}
        self.select('agent:main:one',entries=[user,assistant],activeRunIds=['run'])
        assistant['message']['content'][0]['text']='你好，我是 Mirally。'
        self.select('agent:main:one',entries=[user,assistant],activeRunIds=['run'])
        text=self.shell.chat_text.get('1.0','end')
        self.assertEqual(text.count('你好，我是 Mirally。'),1)
        self.assertIn('你好\n',text)

    def test_unsent_drafts_survive_session_switches(self):
        self.shell.composer.insert('1.0','尚未发送的要求')
        self.shell._select_conversation('agent:main:two')
        self.assertFalse(self.shell.can_stop)
        self.select('agent:main:two')
        self.assertEqual(self.shell.composer.get('1.0','end-1c'),'')
        self.shell._select_conversation('agent:main:one')
        self.select('agent:main:one')
        self.assertEqual(self.shell.composer.get('1.0','end-1c'),'尚未发送的要求')

    def test_failed_send_is_saved_under_its_original_session(self):
        self.assertTrue(self.shell._send_text('不能丢失的补充'))
        identifier=self.transport.requests[-1][0]
        self.select('agent:main:two')
        self.shell._event({'type':'result','id':identifier,'error':'未发送'})
        self.assertEqual(self.shell._drafts['agent:main:one'],'不能丢失的补充')
        self.assertEqual(self.shell.composer.get('1.0','end-1c'),'')

    def test_explicit_retry_reuses_the_send_identity(self):
        self.shell._send_text('重试同一条信息')
        identifier,_,params=self.transport.requests[-1]
        self.shell._event({'type':'result','id':identifier,'error':'连接中断','uncertain':True})
        self.shell._send_text('重试同一条信息')
        self.assertEqual(self.transport.requests[-1][2]['idempotencyKey'],params['idempotencyKey'])

    def test_supplement_uncertainty_does_not_hide_working_state(self):
        self.select('agent:main:one',activeRunIds=['running'],activity={'name':'exec'},
                    error='发送状态待确认',errorType='uncertain')
        self.assertEqual(self.shell._phase,'executing')
        self.assertEqual(self.shell.status_var.get(),'后台工作中')
        self.assertTrue(self.shell.can_stop)

    def test_close_disconnects_only_the_client(self):
        self.select('agent:main:one',activeRunIds=['still-working'])
        self.shell._close()
        self.assertTrue(self.transport.closed)
        self.assertFalse(any(op=='abort' for _,op,_ in self.transport.requests))


if __name__ == '__main__':
    unittest.main()
