"""Threaded handshake results, persistent at-most-once delivery attempts."""
import base64
import email.policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, make_msgid, formatdate
import json
import logging
import re

LOG=logging.getLogger('handshake_worker')


def build_reply(cfg,original,completion):
    raw=base64.urlsafe_b64decode(original['raw']+'===')
    # Revalidate trusted sender and exact task before using any routing metadata.
    from gmail_poller import parse_message
    if parse_message(raw,cfg,sent_by_mailbox='SENT' in original.get('labelIds',[]))!=completion['task_id']:
        raise ValueError('Reply task mismatch')
    source=BytesParser(policy=email.policy.default).parsebytes(raw)
    identifiers=source.get_all('Message-ID',[])
    if len(identifiers)!=1 or not re.fullmatch(r'<[^\s<>\r\n]{1,998}>',str(identifiers[0])):
        raise ValueError('Missing or invalid original Message-ID')
    thread=original.get('threadId','')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',thread):
        raise ValueError('Missing or invalid Gmail thread')
    # Reply only to the authenticated From, never arbitrary Reply-To or CC headers.
    recipient=getaddresses([str(source['From'])])[0][1]
    message=EmailMessage()
    message['From']=cfg['mailbox']
    message['To']=recipient
    message['Subject']='Re: '+str(source['Subject'])
    message['In-Reply-To']=str(identifiers[0])
    message['References']=str(identifiers[0])
    message['Message-ID']=make_msgid(domain=cfg['mailbox'].split('@')[1])
    message['Date']=formatdate(localtime=False)
    message['Auto-Submitted']='auto-replied'
    fields=('task_id','status','outcome','supervisor','repository_detected','repository','branch','head','test_status',
            'github_access_status','started_at','completed_at','codex_exit_code','summary','error')
    report={key:completion.get(key) for key in fields}
    message.set_content('Crypto Radar task results\n\n'+json.dumps(report,indent=2)+'\n')
    return {'threadId':thread,'raw':base64.urlsafe_b64encode(message.as_bytes()).decode('ascii')}


def deliver_pending(state,cfg,gmail,clock):
    if not cfg.get('reply_enabled',False):
        return
    if not gmail.can_send:
        LOG.warning('Replies pending: authorize Gmail send permission; polling remains available')
        return
    rows=state.db.execute("SELECT * FROM replies WHERE status='PENDING' ORDER BY gmail_message_id LIMIT 10").fetchall()
    for row in rows:
        mid=row['gmail_message_id']
        try:
            original=gmail.get('messages/'+mid,{'format':'raw'})
            payload=build_reply(cfg,original,json.loads(row['report_json']))
        except Exception as exc:
            # No send attempted: preserve pending for recoverable reads/configuration.
            LOG.error('Reply preparation failed: %s',type(exc).__name__)
            continue
        with state.db:
            changed=state.db.execute("UPDATE replies SET status='SENDING',attempted_at=? WHERE gmail_message_id=? AND status='PENDING'",
                                     (clock(),mid)).rowcount
        if not changed:
            continue
        try:
            response=gmail.session.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',json=payload,timeout=30)
            response.raise_for_status()
            sent=response.json()
            if not isinstance(sent.get('id'),str) or not sent['id']:
                raise ValueError('Missing send acknowledgment')
            with state.db:
                state.db.execute("UPDATE replies SET status='SENT',sent_at=?,sent_message_id=? WHERE gmail_message_id=?",
                                 (clock(),sent['id'],mid))
            LOG.info('Handshake reply sent')
        except Exception as exc:
            with state.db:
                state.db.execute("UPDATE replies SET status='DELIVERY_UNKNOWN',error=? WHERE gmail_message_id=?",
                                 (type(exc).__name__,mid))
            LOG.error('Reply delivery uncertain; will not automatically resend: %s',type(exc).__name__)
