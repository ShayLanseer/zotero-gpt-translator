import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
from test_translation import t,args,quiet

SPEC=importlib.util.spec_from_file_location('cache_test',Path(t.__file__).with_name('auth_cache.py'))
c=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(c)

class Authorization(unittest.TestCase):
    def api(self):
        api=t.API.__new__(t.API)
        api.base='http://127.0.0.1:23119/api';api.root='/users/0';api.server_id='test-server'
        api.token=None;api.remember=False;api._cache_attempted=True;api._cache_loaded=False;api.auth_cache=Mock()
        api.auth_cache.load.return_value=None
        return api

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_dpapi_encryption_and_binding(self):
        with tempfile.TemporaryDirectory() as td,patch.object(c,'local_app_data',return_value=Path(td)):
            cache=c.CredentialCache('base','root','server');cache.save('fake-test-token-only')
            self.assertEqual(cache.load(),'fake-test-token-only')
            self.assertNotIn('fake-test-token-only',cache.path.read_text())
            other=c.CredentialCache('base','root','other');other.path=cache.path
            with self.assertRaisesRegex(ValueError,'实例'):other.load()
            cache.invalidate();self.assertIsNone(cache.load());self.assertTrue(cache.path.exists())

    @unittest.skipUnless(os.name=='nt','Windows Known Folder')
    def test_real_user_directory_ignores_redirected_environment(self):
        expected=c.local_app_data()
        with patch.dict(os.environ,{'LOCALAPPDATA':r'D:\codex-sandbox\fake','USERPROFILE':r'D:\fake-user'}):
            self.assertEqual(c.local_app_data(),expected)

    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_independent_process_decrypts(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(c,'local_app_data',return_value=Path(td)):
                cache=c.CredentialCache('base','root','server');cache.save('fake-cross-process-token')
            script="import importlib.util,sys;from pathlib import Path;s=importlib.util.spec_from_file_location('c',sys.argv[1]);c=importlib.util.module_from_spec(s);s.loader.exec_module(c);c.local_app_data=lambda:Path(sys.argv[2]);assert c.CredentialCache('base','root','server').load()=='fake-cross-process-token';print('verified')"
            result=subprocess.run([sys.executable,'-c',script,str(Path(c.__file__)),td],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'verified')

    def test_one_time_grant_is_not_saved_or_reused(self):
        api=self.api();api.request=Mock(side_effect=[(200,{'key':'once','remember':False}),(200,{}),(200,{'key':'once2','remember':False}),(200,{})])
        api.write('/items',b'a');self.assertIsNone(api.token);api.write('/items',b'b')
        api.auth_cache.save.assert_not_called();self.assertEqual(api.request.call_count,4)

    def test_always_allow_is_saved_and_cached_on_next_call(self):
        api=self.api();api.request=Mock(side_effect=[(200,{'key':'fake-always','remember':True}),(200,{}),(200,{})])
        api.write('/items',b'a');api.write('/items',b'b')
        api.auth_cache.save.assert_called_once_with('fake-always');self.assertEqual(api.request.call_count,3)
        other=self.api();other.auth_cache.load.return_value='fake-always';other.request=Mock(return_value=(200,{}))
        other.write('/items',b'c');self.assertEqual(other.request.call_count,1)
        self.assertEqual(other.request.call_args.args[3]['Zotero-API-Key'],'fake-always')

    def test_revoked_token_reauthenticates_once_and_preserves_write_token(self):
        api=self.api();api.auth_cache.load.return_value='stale';api.request=Mock(side_effect=[t.APIError(401,'unauthorized'),(200,{'key':'new-fake','remember':True}),(200,{})])
        headers={'Zotero-Write-Token':'unique-test-write'};body=b'exact-same-body'
        api.write('/items',body,headers)
        api.auth_cache.invalidate.assert_called_once()
        first,last=api.request.call_args_list[0],api.request.call_args_list[2]
        self.assertEqual(first.args[:3],last.args[:3]);self.assertEqual(first.args[3]['Zotero-Write-Token'],last.args[3]['Zotero-Write-Token'])

    def test_ambiguous_server_failure_is_not_retried(self):
        api=self.api();api.auth_cache.load.return_value='fake';api.request=Mock(side_effect=t.APIError(500,'server failure'))
        with self.assertRaises(t.APIError):api.write('/items',b'a')
        self.assertEqual(api.request.call_count,1);api.auth_cache.invalidate.assert_not_called()

    def test_save_failure_warns_without_disclosing_key_or_losing_current_grant(self):
        api=self.api();api.auth_cache.save.side_effect=PermissionError('fake-secret-in-error')
        api.request=Mock(side_effect=[(200,{'key':'fake-secret-in-error','remember':True}),(200,{})]);out=io.StringIO()
        with contextlib.redirect_stderr(out):self.assertEqual(api.write('/items',b'a'),(200,{}))
        self.assertIn('无法保存加密授权',out.getvalue());self.assertNotIn('fake-secret-in-error',out.getvalue())

    def test_failed_cache_read_reauthenticates(self):
        api=self.api();api.auth_cache.load.side_effect=ValueError('corrupt')
        api.request=Mock(side_effect=[(200,{'key':'once','remember':False}),(200,{})])
        with contextlib.redirect_stderr(io.StringIO()):api.write('/items',b'a')
        self.assertEqual(api.request.call_count,2)

    def test_reads_do_not_load_write_credentials(self):
        api=self.api();api.request=Mock(return_value=(200,{}));api.get('/items')
        api.auth_cache.load.assert_not_called();api.auth_cache.save.assert_not_called()

if __name__=='__main__':unittest.main()
