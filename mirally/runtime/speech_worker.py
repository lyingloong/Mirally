"""One bounded, cancellable transcription process; credentials stay on stdin."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys


def main():
    request = json.loads(sys.stdin.read(12_000_000))
    audio = base64.b64decode(request['data'], validate=True)
    if not audio or len(audio) > 8 * 1024 * 1024:
        raise ValueError('录音为空或超过 8 MiB。')
    config = request['config']
    name = str(request.get('name') or 'recording.wav')
    suffix = Path(name).suffix.lower()
    if suffix not in {'.wav','.webm','.ogg','.mp3','.m4a','.mp4','.flac','.mpeg'}:
        suffix = { 'audio/wav':'.wav','audio/webm':'.webm','audio/ogg':'.ogg','audio/mpeg':'.mp3','audio/mp4':'.m4a' }.get(request.get('media_type'), '.wav')
    language = request.get('language')
    language = None if not language or language == 'auto' else language
    temporary = request['temp_dir']
    path = Path(temporary) / ('audio' + suffix)
    path.write_bytes(audio)
    if config.get('provider') == 'openai_compatible':
        from mirally.runtime.speech import OpenAICompatibleSpeechProvider
        provider = OpenAICompatibleSpeechProvider(base_url=config.get('base_url',''),api_key=config.get('api_key',''),
                                                   model=config.get('model',''),timeout_seconds=120)
        result = provider.transcribe(path, language=language)
        return {'ok':True,'text':result.text,'language':result.language}
    if config.get('provider') == 'faster_whisper':
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ValueError('本地识别需要安装 faster-whisper。') from None
        model_path = Path(config.get('local_model_path','')).expanduser()
        if not model_path.is_dir():
            raise ValueError('本地语音模型目录不存在。')
        model = WhisperModel(str(model_path),device='cpu',compute_type='int8',local_files_only=True)
        segments, info = model.transcribe(str(path),language=language,vad_filter=True)
        text = ''.join(segment.text for segment in segments).strip()
        return {'ok':True,'text':text,'language':info.language}
    raise ValueError('请先配置语音识别。')


if __name__ == '__main__':
    try:
        result = main()
    except (KeyError, ValueError) as error:
        result = {'ok':False,'error':str(error)[:300]}
    except Exception:
        result = {'ok':False,'error':'语音识别失败，请检查模型、网络和服务设置。'}
    sys.stdout.write(json.dumps(result,ensure_ascii=False))
