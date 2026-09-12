"""Host-configured communication policy; never supplied in model tool arguments."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Contacts:
    email_recipients:frozenset[str]=frozenset()
    chat_channels:frozenset[str]=frozenset()

    def authorize(self,tool,payload):
        if tool=='workspace_email_send':
            recipients=payload.get('to',[])
            return bool(recipients) and all(address.lower() in self.email_recipients for address in recipients)
        if tool=='workspace_chat_send':return payload.get('channel_id') in self.chat_channels
        return False
