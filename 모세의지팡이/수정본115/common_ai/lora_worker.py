"""Optional local inference subprocess; stdin/stdout carry only JSON messages."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import threading
import time


def normalize_messages(messages):
    """Translate the existing Agent history without mutating its conversation."""
    result = copy.deepcopy(messages)
    for message in result:
        for call in message.get('tool_calls') or []:
            function = call.get('function', call)
            args = function.get('arguments')
            if isinstance(args, str):
                function['arguments'] = json.loads(args)
    return result


def decoding_schema(value):
    """Decoder-compatible view; authoritative schema is checked after generation."""
    if isinstance(value, list):
        return [decoding_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: decoding_schema(item) for key, item in value.items()}
    if 'enum' in result:
        groups = {}
        for item in result['enum']:
            kind = ('null' if item is None else 'boolean' if isinstance(item, bool) else
                    'string' if isinstance(item, str) else 'number')
            groups.setdefault(kind, []).append(item)
        if len(groups) > 1 or 'null' in groups or 'boolean' in groups:
            result.pop('enum')
            result.pop('type', None)
            result['anyOf'] = [({'type': kind} if kind in ('null', 'boolean') else {'type': kind, 'enum': items})
                               for kind, items in groups.items()]
    return result


def reply_schema(schema, tools):
    if not tools:
        return schema
    calls = []
    for tool in tools:
        function = tool['function']
        calls.append({'type': 'object', 'additionalProperties': False,
            'required': ['name', 'arguments'], 'properties': {
                'name': {'type': 'string', 'enum': [function['name']]},
                'arguments': function['parameters']}})
    result = {'anyOf': [schema, {'type': 'object', 'additionalProperties': False,
        'required': ['tool_calls'], 'properties': {'tool_calls': {
            'type': 'array', 'minItems': 1, 'items': {'anyOf': calls}}}}]}
    # Local JSON pointers resolve from the document root, including when a
    # canonical response is nested alongside the tool-call alternative.
    if '$defs' in schema:
        result['$defs'] = schema['$defs']
    return result


def parse_reply(text, schema, tools):
    from jsonschema import Draft202012Validator
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise ValueError('로컬 LoRA 출력이 완전한 JSON이 아닙니다. 응답 길이를 확인하세요.') from None
    if not isinstance(data, dict):
        raise ValueError('로컬 LoRA 출력은 JSON 객체여야 합니다.')
    expected = reply_schema(schema, tools)
    if next(Draft202012Validator(expected).iter_errors(data), None) is not None:
        raise ValueError('로컬 LoRA 출력이 요청한 AI 응답 형식을 만족하지 못했습니다.')
    if 'tool_calls' in data:
        calls = [{'id': 'local_' + str(i), 'name': call['name'], 'arguments': call['arguments']}
                 for i, call in enumerate(data['tool_calls'])]
        return {'content': '', 'tool_calls': calls}
    return {'content': json.dumps(data, ensure_ascii=False), 'tool_calls': []}


class Worker:
    def __init__(self):
        self.model = None
        self.tokenizer = None
        self.tokenizer_data = None
        self.settings = None
        self.lease = None

    def load(self, settings, root):
        if self.model is not None:
            return {'loaded': True}
        from common_ai.local_lora import check_adapter, resolve_base_model, validate_local_settings
        settings = validate_local_settings(settings)
        adapter = check_adapter(settings, root)
        base = resolve_base_model(settings['base_model'], root)
        from common_ai.model_runtime import MachineLease
        self.lease = MachineLease('moses_part3_ai_worker.lock')
        self.lease.acquire()
        try:
            import torch
            import transformers
            from peft import PeftModel
            from lmformatenforcer import JsonSchemaParser  # Check optional decoding dependency at load.
            from jsonschema import Draft202012Validator
        except ImportError as exc:
            raise ValueError('로컬 LoRA 실행 패키지가 없습니다. Part3/requirements-local-lora.txt의 선택 설치를 확인하세요.') from exc
        offline = settings.get('local_files_only', True)
        common = {'local_files_only': offline, 'trust_remote_code': False}
        config = transformers.AutoConfig.from_pretrained(base, **common)
        architectures = getattr(config, 'architectures', None) or []
        conditional = any('ConditionalGeneration' in name for name in architectures)
        if conditional:
            loader = getattr(transformers, 'AutoModelForMultimodalLM', None) or getattr(transformers, 'AutoModelForImageTextToText', None)
            if loader is None:
                raise ValueError('이 기본 모델을 지원하는 Transformers 버전이 필요합니다.')
        else:
            loader = transformers.AutoModelForCausalLM
        kwargs = {**common, 'config': config, 'device_map': 'auto', 'torch_dtype': 'auto'}
        if settings.get('load_in_4bit', False):
            if not torch.cuda.is_available():
                raise ValueError('4비트 로딩에는 지원되는 CUDA GPU와 bitsandbytes가 필요합니다. 4비트를 끄거나 실행 환경을 확인하세요.')
            try:
                kwargs['quantization_config'] = transformers.BitsAndBytesConfig(load_in_4bit=True)
            except ImportError as exc:
                raise ValueError('4비트 실행 패키지 bitsandbytes를 확인하세요.') from exc
        # Always attach the configured adapter to the configured architecture.
        model = loader.from_pretrained(base, **kwargs)
        self.model = PeftModel.from_pretrained(model, str(adapter), local_files_only=True, is_trainable=False)
        self.model.eval()
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(base, **common)
        if not self.tokenizer.chat_template:
            raise ValueError('기본 모델에 대화 형식(chat template)이 없습니다.')
        from lmformatenforcer import TokenEnforcerTokenizerData
        # Use the public core API: older LFE Transformers integrations import a
        # tokenizer module that Transformers 5 removed. Cache vocabulary once.
        zero = self.tokenizer.encode('0', add_special_tokens=False)[-1]
        specials = set(self.tokenizer.all_special_ids)
        tokens = []
        for index in range(len(self.tokenizer)):
            if index in specials:
                continue
            decoded = self.tokenizer.decode([zero, index])[1:]
            plain = self.tokenizer.decode([index])
            tokens.append((index, decoded, len(decoded) > len(plain)))
        self.tokenizer_data = TokenEnforcerTokenizerData(tokens,
            lambda ids: self.tokenizer.decode(ids, clean_up_tokenization_spaces=False).rstrip('\ufffd'),
            self.tokenizer.eos_token_id, False, len(self.tokenizer))
        self.settings = settings
        return {'loaded': True}

    def chat(self, messages, tools, response_schema):
        if self.model is None:
            raise ValueError('로컬 LoRA 모델이 아직 로드되지 않았습니다.')
        import torch
        from lmformatenforcer import JsonSchemaParser, TokenEnforcer
        from lmformatenforcer.characterlevelparser import CharacterLevelParserConfig
        history = normalize_messages(messages)
        schema = reply_schema(response_schema, tools)
        instruction = ('응답은 다음 JSON schema를 만족하는 JSON 객체 하나만 반환하세요. '
            '조회가 필요하면 tool_calls에 도구명과 arguments를 반환하고, 최종 해석은 원래 응답 형식으로 반환하세요. '
            '설명, Markdown, XML 도구 태그를 쓰지 마세요.\n' + json.dumps(schema, ensure_ascii=False))
        # Avoid the model template's native XML tool-call instruction conflicting
        # with JSON-constrained decoding; the trusted Agent tool loop is unchanged.
        if tools:
            instruction += '\n사용할 수 있는 읽기 전용 도구:\n' + json.dumps(tools, ensure_ascii=False)
        if history and history[0].get('role') == 'system':
            history[0]['content'] = str(history[0].get('content') or '') + '\n' + instruction
        else:
            history.insert(0, {'role': 'system', 'content': instruction})
        rendered = self.tokenizer.apply_chat_template(history, tokenize=False,
            add_generation_prompt=True, enable_thinking=False)
        inputs = self.tokenizer(rendered, return_tensors='pt', add_special_tokens=False)
        inputs = inputs.to(self.model.get_input_embeddings().weight.device)
        # Do not truncate the strategy contract or previous canonical intent.
        max_tokens = self.settings.get('max_new_tokens', 4096)
        limits = [getattr(self.tokenizer, 'model_max_length', None),
                  getattr(self.model.config, 'max_position_embeddings', None),
                  getattr(getattr(self.model.config, 'text_config', None), 'max_position_embeddings', None)]
        limit = min((v for v in limits if isinstance(v, int) and 0 < v < 10**9), default=None)
        if limit and inputs['input_ids'].shape[-1] + max_tokens > limit:
            raise ValueError('대화가 모델의 처리 길이를 초과합니다. 새 대화를 시작하거나 응답 길이를 줄여 주세요.')
        parser_config = CharacterLevelParserConfig()
        # Schema has unbounded steps/branches. LFE's default array limit of 20
        # and ASCII alphabet must not silently change that language contract.
        parser_config.max_json_array_length = sys.maxsize
        parser_config.alphabet = ''.join(sorted(set(parser_config.alphabet + rendered +
            ''.join(chr(i) for i in range(0xAC00, 0xD7A4)) +
            ''.join(chr(i) for i in range(0x1100, 0x1200)) +
            ''.join(chr(i) for i in range(0x3130, 0x3190)))))
        parser = JsonSchemaParser(decoding_schema(schema), config=parser_config)
        enforcer = TokenEnforcer(self.tokenizer_data, parser)
        # LFE initializes its own alphabet/config inside TokenEnforcer. Restore
        # our unbounded array contract afterwards, including the vocab alphabet.
        parser_config.alphabet = ''.join(sorted(set(parser_config.alphabet + self.tokenizer_data.tokenizer_alphabet)))
        parser.config = parser_config
        def allowed(batch_id, sent):
            tokens = enforcer.get_allowed_tokens(sent.tolist())
            return getattr(tokens, 'allowed_tokens', tokens)
        with torch.inference_mode():
            output = self.model.generate(**inputs, max_new_tokens=max_tokens,
                do_sample=False, prefix_allowed_tokens_fn=allowed,
                pad_token_id=self.tokenizer.pad_token_id if self.tokenizer.pad_token_id is not None else self.tokenizer.eos_token_id)
        text = self.tokenizer.decode(output[0][inputs['input_ids'].shape[-1]:], skip_special_tokens=True)
        return parse_reply(text, response_schema, tools)


def watch_parent(pid):
    def watch():
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                os._exit(0)
            kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
            os._exit(0)
        while os.getppid() == pid:
            time.sleep(0.1)
        os._exit(0)
    threading.Thread(target=watch, daemon=True).start()


def main():
    import argparse
    import contextlib
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent-pid', type=int, required=True)
    args = parser.parse_args()
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    watch_parent(args.parent_pid)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    worker = Worker()
    wire = sys.stdout
    # Native libraries and progress bars cannot corrupt the JSON protocol.
    with contextlib.redirect_stdout(sys.stderr):
        for line in sys.stdin:
            request_id = None
            try:
                request = json.loads(line)
                request_id = request['id']
                if request['op'] == 'load':
                    result = worker.load(request['settings'], Path(request['root']))
                elif request['op'] == 'chat':
                    result = worker.chat(request['messages'], request['tools'], request['response_schema'])
                else:
                    raise ValueError('알 수 없는 로컬 LoRA 요청입니다.')
                reply = {'id': request_id, 'ok': True, 'result': result}
            except Exception as exc:
                if isinstance(exc, ValueError) and str(exc).startswith(('로컬', 'LoRA', '기본', '모델', '이 기본', '4비트', '대화')):
                    error = str(exc)
                elif 'outofmemory' in type(exc).__name__.lower() or 'out of memory' in str(exc).lower():
                    error = '로컬 LoRA 실행 메모리가 부족합니다. 4비트 설정과 GPU 메모리를 확인하세요.'
                else:
                    error = ('로컬 LoRA 모델 로딩·추론 실패 (' + type(exc).__name__ +
                             '). 기본 모델 캐시, LoRA 호환성, 선택 실행 패키지를 확인하세요.')
                reply = {'id': request_id, 'ok': False, 'error': error}
            wire.write(json.dumps(reply, ensure_ascii=False) + '\n')
            wire.flush()


if __name__ == '__main__':
    main()
