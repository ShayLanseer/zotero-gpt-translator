"""Per-user, per-Zotero-instance credentials. Never store plaintext keys."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import uuid


class GUID(ctypes.Structure):
    _fields_ = [('data', ctypes.c_ubyte * 16)]


class BLOB(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def local_app_data():
    # Do not trust LOCALAPPDATA, USERPROFILE or HOME: execution hosts redirect them.
    if os.name != 'nt':
        raise OSError('持久授权目前需要 Windows 用户加密存储。')
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    folder = GUID((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID('f1b32785-6fba-4fcf-9d55-7b8e7f157091').bytes_le))
    result = ctypes.c_void_p()
    hr = shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(result))
    if hr < 0:
        raise OSError('Windows 无法提供当前用户的 LocalAppData 目录。')
    try:
        path = Path(ctypes.wstring_at(result))
        if not path.is_absolute():
            raise OSError('Windows 返回的用户目录不是绝对路径。')
        return path
    finally:
        ole.CoTaskMemFree(result)


def crypt(data, entropy, decrypt=False):
    if os.name != 'nt':
        raise OSError('当前系统不支持 Windows DPAPI。')
    dll = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    def blob(raw):
        buf = ctypes.create_string_buffer(raw)
        return BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf
    value, value_buf = blob(data)
    salt, salt_buf = blob(entropy)
    output = BLOB()
    func = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    func.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    func.restype = wintypes.BOOL
    if not func(ctypes.byref(value), None, ctypes.byref(salt), None, None, 1, ctypes.byref(output)):
        raise OSError('Windows DPAPI 加密授权校验失败。')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class CredentialCache:
    def __init__(self, base, root, server_id):
        self.binding = {'base': base, 'root': root, 'server_id': server_id}
        self.identity = json.dumps(self.binding, sort_keys=True, separators=(',', ':')).encode()
        self.entropy = hashlib.sha256(b'zotero-gpt-translator/v1/' + self.identity).digest()
        self.path = local_app_data() / 'Lanseer' / 'ZoteroGPTTranslator' / 'authorization' / (self.entropy.hex() + '.dpapi.json')

    def load(self):
        if not self.path.exists():
            return None
        if self.path.stat().st_size > 262144:
            raise ValueError('授权缓存大小异常。')
        record = json.loads(self.path.read_text(encoding='utf8'))
        if record.get('schema') != 1 or record.get('binding') != self.binding:
            raise ValueError('授权缓存实例不匹配。')
        if record.get('invalidated') is True:
            return None
        data = json.loads(crypt(base64.b64decode(record['encrypted'], validate=True), self.entropy, decrypt=True))
        if data.get('binding') != self.binding or data.get('remember') is not True or not isinstance(data.get('key'), str) or not data['key']:
            raise ValueError('授权缓存内容无效。')
        return data['key']

    def _write(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic publish; on failure retain the temporary file rather than deleting it.
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(record, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def save(self, key):
        if not isinstance(key, str) or not key:
            raise ValueError('不能缓存空授权。')
        raw = json.dumps({'key': key, 'remember': True, 'binding': self.binding}).encode()
        encrypted = base64.b64encode(crypt(raw, self.entropy)).decode('ascii')
        self._write({'schema': 1, 'binding': self.binding, 'encrypted': encrypted})
        if self.load() != key:
            raise ValueError('授权缓存写后读取验证失败。')

    def invalidate(self):
        # Keep an audit tombstone, without retaining an invalid credential.
        if self.path.exists():
            self._write({'schema': 1, 'binding': self.binding, 'invalidated': True})
