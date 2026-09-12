"""Allowlisted Ambiguous CLI tools. Content is stdin JSON, never shell code."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Literal
from uuid import UUID
from pydantic import BaseModel,ConfigDict,Field

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class Empty(Input):pass
class Resource(Input):id:UUID
class Search(Input):
    q:str=Field(default='',max_length=1000)
    limit:int=Field(default=20,ge=1,le=100)
class TaskCreate(Input):
    title:str=Field(min_length=1,max_length=500)
    description:str=Field(default='',max_length=20000)
    priority:Literal['urgent','high','medium','low']='medium'
    assignee_id:UUID|None=None
class TaskUpdate(Resource):
    status:Literal['todo','in_progress','done','cancelled','blocked']
class Report(Input):
    title:str=Field(min_length=1,max_length=500)
    content:str=Field(min_length=1,max_length=100000,description="Report text with paragraphs separated by blank lines")
class ReportUpdate(Report):id:UUID
class SheetCreate(Input):title:str=Field(min_length=1,max_length=500)
class SheetRows(Resource):
    rows:list[dict[str,str|int|float|bool|None]]=Field(min_length=1,max_length=100)
    sheet:str|None=None
class Mail(Input):
    to:list[str]=Field(default_factory=list,max_length=30)
    subject:str=Field(min_length=1,max_length=500)
    body_markdown:str=Field(min_length=1,max_length=100000)
class Chat(Input):
    channel_id:UUID
    content:str=Field(min_length=1,max_length=20000)
    thread_id:UUID|None=None

# Explicit names and models, not a generic CLI or arbitrary HTTP escape hatch.
TOOLS={
 'workspace_tasks_list':(Search,('tasks','list'),False),
 'workspace_task_get':(Resource,('tasks','get'),False),
 'workspace_task_create':(TaskCreate,('tasks','create'),True),
 'workspace_task_update':(TaskUpdate,('tasks','update'),True),
 'workspace_report_create':(Report,('docs','create'),True),
 'workspace_report_get':(Resource,('docs','get'),False),
 'workspace_report_update':(ReportUpdate,('docs','update'),True),
 'workspace_sheets_list':(Empty,('sheets','list'),False),
 'workspace_sheet_create':(SheetCreate,('sheets','create'),True),
 'workspace_sheet_get':(Resource,('sheets','get'),False),
 'workspace_sheet_append':(SheetRows,('sheets','rows','add'),True),
 'workspace_email_search':(Search,('mail','search'),False),
 'workspace_email_get':(Resource,('mail','get'),False),
 'workspace_email_draft':(Mail,('mail','drafts','create'),True),
 'workspace_email_send':(Mail,('mail','send'),True),
 'workspace_chat_send':(Chat,('chat','messages','send'),True),
}

class AmbiguousCLI:
    def __init__(self,cwd,user_id,workspace_id):
        self.cwd=Path(cwd).resolve()
        self.user_id=str(UUID(user_id));self.workspace_id=str(UUID(workspace_id))
        self.command=('npx','--yes','ambiguous@0.9.0')
        self.env={k:v for k,v in os.environ.items() if k in
            ('PATH','HOME','NODE_EXTRA_CA_CERTS','AMBI_API_TOKEN','AMBI_API_URL')}

    def run(self,command,payload=None):
        try:
            result=subprocess.run([*self.command,*command],cwd=self.cwd,env=self.env,
                input=None if payload is None else json.dumps(payload,allow_nan=False),
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=45)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('Ambiguous timed out; outcome may be uncertain; do not retry a write') from exc
        if result.returncode or len(result.stdout)>2_000_000:
            raise RuntimeError('Ambiguous operation failed; inspect identity, permission or service availability')
        try:data=json.loads(result.stdout)
        except ValueError as exc:raise RuntimeError('Invalid Ambiguous response') from exc
        if isinstance(data,dict) and data.get('ok') is False:raise RuntimeError('Ambiguous rejected the operation')
        return data

    def verify_identity(self):
        identity=self.run(('whoami',))
        if (identity.get('authenticated') is not True or identity.get('userId')!=self.user_id
            or identity.get('workspaceId')!=self.workspace_id
            or identity.get('apiUrl','').rstrip('/')!='https://app.ambiguous.ai'):
            raise RuntimeError('Ambiguous identity/workspace mismatch; operation denied')

class WorkspaceTools:
    def __init__(self,cli,journal,authorize_delivery=lambda tool,payload:False):
        self.cli=cli;self.journal=journal;self.authorize_delivery=authorize_delivery
        self.scope='workspace:'+cli.workspace_id+':'+cli.user_id
        self.lock=threading.Lock()

    def definitions(self):
        return [{'type':'function','function':{'name':name,
            'description':('Write' if write else 'Read')+' Ambiguous workspace data. '+
                ('Delivery requires host-verified authorization for the exact payload.' if name in ('workspace_email_send','workspace_chat_send') else ''),
            'parameters':model.model_json_schema()}} for name,(model,_,write) in TOOLS.items()]

    def execute(self,name,payload,request_id):
        if name not in TOOLS:raise ValueError('Unknown workspace tool')
        model,command,write=TOOLS[name]
        data=model.model_validate(payload).model_dump(mode='json',exclude_none=True)
        if not request_id or len(request_id)>200:raise ValueError('request_id required')
        delivery=name in ('workspace_email_send','workspace_chat_send') or (
            name=='workspace_task_create' and data.get('assignee_id') not in (None,self.cli.user_id))
        if delivery and not self.authorize_delivery(name,data):
            return {'status':'needs_authorization','tool':name,'proposal':data}
        if name=='workspace_email_send' and not data['to']:raise ValueError('Sending requires recipients')
        with self.lock:
            self.cli.verify_identity()
            if not write:return {'status':'ok','data':self.invoke(name,command,data,request_id)}
            fingerprint=hashlib.sha256(json.dumps([name,data],sort_keys=True).encode()).hexdigest()
            claim=self.journal.request(op='claim',robot=self.scope,request=request_id,incident=request_id,
                action=name,fingerprint=fingerprint,limit=1)
            if not claim['claimed']:
                return {'status':'not_repeated','reason':claim['reason'],'prior':claim.get('prior')}
            result=None
            try:
                result=self.invoke(name,command,data,request_id)
                self.last_verification=None
                verified=self.verify_write(name,data,result)
                outcome={'status':'ok' if verified else 'unknown','verified':verified,'data':result}
                if self.last_verification is not None:outcome['verification']=self.last_verification
            except Exception as exc:
                outcome={'status':'unknown','verified':False,'error':str(exc),'data':result,
                    'reason':'Write was claimed; reconcile it before any retry'}
            self.journal.request(op='finish',robot=self.scope,request=request_id,status=outcome['status'],result=outcome)
            return outcome

    def invoke(self,name,command,data,request_id):
        data=dict(data)
        if 'id' in data:command=(*command,data.pop('id'))
        if name=='workspace_report_create':data.update(type='doc',visibility='restricted')
        if name=='workspace_sheet_create':data['visibility']='restricted'
        if name=='workspace_report_update':self.cli.run(('docs','get',str(data.get('id',command[-1]))))
        if name=='workspace_task_create':data.setdefault('assignee_id',self.cli.user_id)
        if name=='workspace_chat_send':command=(*command,data.pop('channel_id'))
        if name in ('workspace_email_draft','workspace_email_send'):
            data['idempotency_key']=hashlib.sha256((self.scope+request_id).encode()).hexdigest()
        result=self.cli.run(command,data)
        if name.startswith('workspace_task_') and isinstance(result,dict):return result.get('task',result)
        return result

    def verify_write(self,name,data,result):
        if not isinstance(result,dict):return False
        resource_id=result.get('id') or data.get('id')
        if name=='workspace_sheet_append':
            # Append APIs may return counts rather than the document; confirm through readback.
            sheet=self.cli.run(('sheets','get',data['id']))
            body=sheet.get('data',{})
            tabs=body.get('sheets',body.get('tabs',[]))
            if isinstance(tabs,dict):tabs=[dict(v,name=k) for k,v in tabs.items()]
            if not tabs and 'rows' in body:tabs=[body]
            for tab in tabs:
                if data.get('sheet') and tab.get('name')!=data['sheet']:continue
                rows=tab.get('rows',[])
                if all(any(all(row.get(k)==v for k,v in wanted.items()) for row in rows if isinstance(row,dict)) for wanted in data['rows']):return True
            return False
        if not resource_id:return False
        if name.startswith('workspace_task_'):
            current=self.cli.run(('tasks','get',str(resource_id)))
            current=current.get('task',current)
            fields=['title','description','priority'] if name.endswith('create') else ['status']
            return all(current.get(k)==data[k] for k in fields)
        if name.startswith('workspace_report_'):
            current=self.cli.run(('docs','get',str(resource_id)))
            content=current.get('content')
            if isinstance(content,str):
                try:
                    document=json.loads(content)
                    if document.get('type')=='doc':
                        def text(node):
                            if node.get('type')=='text':return node.get('text','')
                            if node.get('type')=='hardBreak':return '\n'
                            return ''.join(text(child) for child in node.get('content',[]))
                        content='\n\n'.join(text(block) for block in document.get('content',[]))
                except (ValueError,AttributeError,TypeError):pass
            return current.get('title')==data['title'] and content==data['content']
        if name=='workspace_sheet_create':
            current=self.cli.run(('sheets','get',str(resource_id)))
            return current.get('title')==data['title'] and isinstance(current.get('data'),dict)
        if name in ('workspace_email_draft','workspace_email_send'):
            current=self.cli.run(('mail','get',str(resource_id)))
            self.last_verification={'id':current.get('id'),'delivery_status':current.get('delivery_status'),
                'folder':current.get('folder'),'sent_at':current.get('sent_at'),'subject':current.get('subject'),'to':current.get('to')}
            recipients=[r.get('email') if isinstance(r,dict) else r for r in current.get('to',[])]
            body=current.get('body_markdown') or current.get('body_text') or ''
            content_ok=(current.get('subject')==data['subject'] and recipients==data['to']
                and body.strip()==data['body_markdown'].strip())
            if name=='workspace_email_draft':return content_ok and current.get('folder')=='drafts'
            return content_ok and current.get('delivery_status')=='sent'
        if name=='workspace_chat_send':return result.get('content')==data['content'] and result.get('channel_id')==data['channel_id']
        return False
