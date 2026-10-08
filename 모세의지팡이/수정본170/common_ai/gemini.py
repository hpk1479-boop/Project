"""Optional Google REST provider; model and credentials come from common settings.

The JSON tool envelope is the same as local providers, so the existing Agent
executes only its declared tools and keeps its authoritative schema unchanged.
REST contracts: https://ai.google.dev/api/generate-content
Structured outputs: https://ai.google.dev/gemini-api/docs/structured-output
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode


API_ROOT='https://generativelanguage.googleapis.com/v1beta/models/'
MAX_REPLY_BYTES=4*1024*1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward authentication to a redirect target, even on the same host."""
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        return None


def _open_request(request,*,timeout):
    return urllib.request.build_opener(_NoRedirect()).open(request,timeout=timeout)


def validate_settings(settings,*,required=False):
    result=dict(settings)
    model=result.get('gemini_model')
    if model is None or model=='':
        result.pop('gemini_model',None)
        if required:raise ValueError('Gemini 모델명을 입력하세요.')
    else:
        if not isinstance(model,str):raise ValueError('Gemini 모델명을 확인하세요.')
        model=model.strip().lower()
        if model.startswith('models/'):model=model[len('models/'):]
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',model):
            raise ValueError('Gemini 모델명에는 영문·숫자·점·밑줄·하이픈만 사용할 수 있습니다.')
        result['gemini_model']=model
    key=result.get('gemini_api_key')
    if key is None or key=='':
        result.pop('gemini_api_key',None)
        if required:raise ValueError('Gemini 연결 키를 입력하세요.')
    else:
        if not isinstance(key,str):raise ValueError('Gemini 연결 키를 확인하세요.')
        key=key.strip()
        if not key or len(key)>4096 or any(not 33<=ord(char)<=126 for char in key):
            raise ValueError('Gemini 연결 키 형식을 확인하세요. 공백·제어 문자는 사용할 수 없습니다.')
        result['gemini_api_key']=key
    timeout=result.get('timeout',90)
    if type(timeout) is not int or not 10<=timeout<=300:
        raise ValueError('AI 응답 제한 시간은 10~300의 정수여야 합니다.')
    result['timeout']=timeout
    tokens=result.get('max_new_tokens')
    if tokens is not None and (type(tokens) is not int or not 1<=tokens<=32768):
        raise ValueError('AI 응답 길이는 비우거나 1~32768의 정수여야 합니다.')
    return result


def _http_error_message(exc):
    from .external_errors import http_error_message
    return http_error_message('gemini', exc)


def check_api_key(settings):
    """Check a replacement key before saving; send no strategy or project data."""
    checked=validate_settings(settings)
    if not checked.get('gemini_api_key'):raise ValueError('Gemini 연결 키를 입력하세요.')
    request=urllib.request.Request(API_ROOT+'?pageSize=1',
        headers={'x-goog-api-key':checked['gemini_api_key']},method='GET')
    try:
        with _open_request(request,timeout=min(15,checked['timeout'])) as response:
            payload=response.read(65537)
        if len(payload)>65536 or not isinstance(json.loads(payload),dict):
            raise ValueError('Gemini API 키 확인 응답이 올바르지 않습니다.')
    except urllib.error.HTTPError as exc:
        raise ValueError(_http_error_message(exc)+' (HTTP '+str(exc.code)+')') from None
    except (TimeoutError,urllib.error.URLError,OSError) as exc:
        from .external_errors import connection_error_message
        raise ValueError(connection_error_message('gemini',exc)) from None
    except (UnicodeError,json.JSONDecodeError):
        raise ValueError('Gemini API 키 확인 응답이 올바르지 않습니다.') from None


def list_models(settings):
    """Read callable model IDs only; credentials stay in the Google header."""
    fallback = {'available': False, 'models': [],
                'error': 'Gemini 모델 목록을 불러오지 못했습니다. 모델명을 직접 입력하세요.'}
    try:
        checked = validate_settings({name: settings[name] for name in
                                     ('gemini_api_key', 'timeout') if name in settings})
        key = checked.get('gemini_api_key')
        if not key:
            return {**fallback, 'error': 'API 키를 입력하면 모델 목록을 불러옵니다. 모델명을 직접 입력할 수도 있습니다.'}
        deadline = time.monotonic() + min(10, checked['timeout'])
        names, seen_tokens, token = set(), set(), None
        for _ in range(10):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return fallback
            query = {'pageSize': 100}
            if token:
                query['pageToken'] = token
            request = urllib.request.Request(API_ROOT + '?' + urlencode(query),
                headers={'x-goog-api-key': key}, method='GET')
            with _open_request(request, timeout=remaining) as response:
                payload = response.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                return fallback
            data = json.loads(payload)
            rows = data.get('models', [])
            if not isinstance(rows, list):
                return fallback
            for row in rows:
                if not isinstance(row, dict) or 'generateContent' not in row.get('supportedGenerationMethods', []):
                    continue
                name = row.get('name')
                if isinstance(name, str) and name.startswith('models/'):
                    model = name[len('models/'):]
                    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', model) and key not in model:
                        names.add(model)
            token = data.get('nextPageToken')
            if token is not None and not isinstance(token, str):
                return fallback
            if not token:
                return {'available': True, 'models': sorted(names), 'error': None}
            if not isinstance(token, str) or len(token) > 4096 or token in seen_tokens or key in token:
                return fallback
            seen_tokens.add(token)
        return fallback
    except urllib.error.HTTPError as exc:
        return {**fallback, 'error': _http_error_message(exc) + ' (HTTP ' + str(exc.code) + ')'}
    except (OSError, ValueError, TypeError, AttributeError):
        return fallback


def _has_schema_references(value):
    """Referenced strategy contracts can exceed the native decoder's subset.

    Use Google's JSON MIME mode for these contracts, keep their full schema in
    the prompt, and validate with the original schema before returning anything.
    Simple contracts (including WATCH) retain native schema-constrained decoding.
    The choice is structural, independent of model name and strategy identifiers.
    """
    if isinstance(value,dict):
        return '$ref' in value or '$defs' in value or any(_has_schema_references(item) for item in value.values())
    if isinstance(value,list):return any(_has_schema_references(item) for item in value)
    return False


def _decoder_schema(value):
    """Google's supported decoder view; full original schema is checked afterwards."""
    if isinstance(value,list):return [_decoder_schema(item) for item in value]
    if not isinstance(value,dict):return value
    allowed={'$id','$defs','$ref','$anchor','type','format','title','description','enum',
             'items','prefixItems','minItems','maxItems','minimum','maximum',
             'anyOf','oneOf','properties','additionalProperties','required'}
    result={}
    for key,item in value.items():
        if key not in allowed:continue
        if key in ('properties','$defs'):
            result[key]={name:_decoder_schema(sub) for name,sub in item.items()}
        else:result[key]=_decoder_schema(item)
    if 'const' in value:result['enum']=[value['const']]
    return result


def _history(messages):
    from .reply_format import normalize_messages
    instructions=[];contents=[]
    for message in normalize_messages(messages):
        role=message.get('role')
        content=message.get('content') or ''
        if not isinstance(content,str):
            raise ValueError('Gemini 대화는 텍스트 형식이어야 합니다.')
        if role=='system':instructions.append(content);continue
        if role=='assistant':
            calls=message.get('tool_calls') or []
            if calls:
                items=[]
                for call in calls:
                    function=call.get('function',call)
                    items.append({'name':function['name'],'arguments':function.get('arguments') or {}})
                content=json.dumps({'tool_calls':items},ensure_ascii=False)
            target='model'
        elif role=='tool':
            content=json.dumps({'tool_result':{'name':message.get('name'),
                'tool_call_id':message.get('tool_call_id'),'content':content}},ensure_ascii=False)
            target='user'
        elif role=='user':target='user'
        else:raise ValueError('Gemini 대화 역할이 올바르지 않습니다.')
        if content:
            parts=[{'text':content}]
            if contents and contents[-1]['role']==target:contents[-1]['parts'].extend(parts)
            else:contents.append({'role':target,'parts':parts})
    if not contents:raise ValueError('Gemini에 보낼 대화가 없습니다.')
    return instructions,contents


class Gemini:
    permission_scope='external'
    def __init__(self,settings):
        self.settings=validate_settings(settings,required=True)
        self.model=self.settings['gemini_model']
        self.timeout=float(self.settings['timeout'])
        from .model_runtime import RUNTIME
        self.generation=RUNTIME.generation

    def chat(self,messages,tools,*,response_schema=None):
        from .model_runtime import RUNTIME
        from .provider import default_schema
        schema=default_schema() if response_schema is None else response_schema
        return RUNTIME.remote_chat(self,lambda deadline:self._chat(messages,tools,schema,deadline))

    def _chat(self,messages,tools,schema,deadline):
        from .external_prompt import prepare
        messages,tools,schema=prepare(messages,tools,schema,self.settings)
        from .reply_format import decoding_schema,parse_reply,reply_schema
        expected=reply_schema(schema,tools)
        instructions,contents=_history(messages)
        instructions.append('Return one JSON object. Use tool_calls only for the listed read-only tools; otherwise return the original result object. No Markdown or XML.')
        body={'contents':contents,'systemInstruction':{'parts':[{'text':'\n'.join(instructions)}]},
              'generationConfig':{'temperature':0.1,'responseMimeType':'application/json'}}
        if not _has_schema_references(expected):
            body['generationConfig']['responseJsonSchema']=_decoder_schema(decoding_schema(expected))
        if self.settings.get('max_new_tokens') is not None:
            body['generationConfig']['maxOutputTokens']=self.settings['max_new_tokens']
        remaining=deadline-time.monotonic()
        if remaining<=0:raise ValueError('Gemini 응답 대기 시간이 초과되었습니다.')
        request=urllib.request.Request(API_ROOT+self.model+':generateContent',
            data=json.dumps(body,ensure_ascii=False).encode('utf-8'),method='POST',
            headers={'Content-Type':'application/json','x-goog-api-key':self.settings['gemini_api_key']})
        try:
            with _open_request(request,timeout=remaining) as response:
                payload=response.read(MAX_REPLY_BYTES+1)
        except urllib.error.HTTPError as exc:
            message=_http_error_message(exc)
            raise ValueError(message+' (HTTP '+str(exc.code)+')') from None
        except (TimeoutError,urllib.error.URLError,OSError) as exc:
            from .external_errors import connection_error_message
            raise ValueError(connection_error_message('gemini',exc)) from None
        if len(payload)>MAX_REPLY_BYTES:raise ValueError('Gemini 응답이 너무 큽니다. 응답 길이 설정을 확인하세요.')
        try:
            data=json.loads(payload.decode('utf-8'))
            candidates=data.get('candidates')
            if not isinstance(candidates,list) or not candidates:
                raise ValueError('Gemini가 응답을 반환하지 않았습니다. 요청 내용 또는 모델 사용 조건을 확인하세요.')
            candidate=candidates[0]
            finish=candidate.get('finishReason')
            if finish=='MAX_TOKENS':raise ValueError('Gemini 응답 길이가 부족합니다. 응답 길이 설정을 늘려주세요.')
            if finish not in (None,'STOP'):
                raise ValueError('Gemini가 응답을 완료하지 못했습니다. 요청 내용 또는 모델 사용 조건을 확인하세요.')
            parts=candidate['content']['parts']
            if not isinstance(parts,list):raise TypeError()
            text=''.join(part['text'] for part in parts if not part.get('thought') and isinstance(part.get('text'),str))
        except (UnicodeError,json.JSONDecodeError,AttributeError,KeyError,TypeError):
            raise ValueError('Gemini 응답 형식이 올바르지 않습니다.') from None
        # Authentication is not model context. A server must not be able to echo
        # its request header into a schema-valid strategy, tool result or UI log.
        if self.settings['gemini_api_key'] in text:
            raise ValueError('Gemini 응답에 연결 인증정보가 포함되어 응답을 차단했습니다.') from None
        try:
            result=parse_reply(text,schema,tools)
        except ImportError:
            raise ValueError('AI JSON 검증 패키지가 없습니다. jsonschema 설치를 확인하세요.') from None
        except ValueError:
            from .external_errors import ReplyValidationError, reply_error_message
            raise ReplyValidationError(reply_error_message('gemini',text,schema,tools),text) from None
        if self.settings['gemini_api_key'] in json.dumps(result,ensure_ascii=False):
            raise ValueError('Gemini 응답에 연결 인증정보가 포함되어 응답을 차단했습니다.') from None
        return result
