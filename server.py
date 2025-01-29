# mailtrap/smtp_server.py
import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from aiosmtpd.controller import Controller
from email import message_from_bytes
import html2text
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


class MailTrapHandler:
    def __init__(self, storage_dir='./mail_storage'):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(exist_ok=True)
        self.emails_file = self.storage_dir / 'emails.json'
        self.attachments_dir = self.storage_dir / 'attachments'
        self.attachments_dir.mkdir(exist_ok=True)
        
        # Initialize or load existing emails
        if self.emails_file.exists():
            with open(self.emails_file, 'r') as f:
                self.emails = json.load(f)
        else:
            self.emails = []

    async def handle_RCPT(
        self, 
        server, 
        session, 
        envelope, 
        address, 
        rcpt_options
    ):
        envelope.rcpt_tos.append(address)
        return '250 OK'

    async def handle_DATA(self, server, session, envelope):
        try:
            email_id = len(self.emails) + 1
            mail_from = envelope.mail_from
            rcpt_tos = envelope.rcpt_tos
            
            # Parse the email message
            msg = message_from_bytes(envelope.content)
            
            # Extract basic email information
            subject = msg.get('subject', '')
            date = msg.get('date', datetime.now().isoformat())
            
            # Handle multipart messages and attachments
            attachments = []
            body_html = None
            body_text = None

            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get('Content-Disposition'))

                    # Handle attachments
                    if 'attachment' in content_disposition:
                        filename = part.get_filename()
                        if filename:
                            # Save attachment
                            attachment_path = self.attachments_dir / (
                                f'{email_id}_{filename}'
                            )
                            with open(attachment_path, 'wb') as f:
                                f.write(part.get_payload(decode=True))
                            attachments.append({
                                'filename': filename,
                                'path': str(attachment_path),
                                'content_type': content_type
                            })
                    else:
                        # Handle email body
                        if content_type == 'text/html':
                            body_html = part.get_payload(decode=True).decode()
                            if not body_text:
                                h = html2text.HTML2Text()
                                body_text = h.handle(body_html)
                        elif content_type == 'text/plain':
                            body_text = part.get_payload(decode=True).decode()
            else:
                # Handle non-multipart messages
                content_type = msg.get_content_type()
                if content_type == 'text/html':
                    body_html = msg.get_payload(decode=True).decode()
                    h = html2text.HTML2Text()
                    body_text = h.handle(body_html)
                else:
                    body_text = msg.get_payload(decode=True).decode()

            # Create email record
            email_data = {
                'id': email_id,
                'from': mail_from,
                'to': rcpt_tos,
                'subject': subject,
                'date': date,
                'content_text': body_text,
                'content_html': body_html,
                'attachments': attachments,
                'headers': dict(msg.items())
            }

            # Save to storage
            self.emails.append(email_data)
            with open(self.emails_file, 'w') as f:
                json.dump(self.emails, f, indent=2)

            return '250 Message accepted for delivery'
        
        except Exception as e:
            print(f"Error processing email: {str(e)}")
            return '500 Error processing message'


class MailTrap:
    def __init__(self, host='127.0.0.1', port=1024, 
                 storage_dir='./mail_storage'):
        self.host = host
        self.port = port
        self.handler = MailTrapHandler(storage_dir)
        self.controller = None

    def start(self):
        self.controller = Controller(
            self.handler,
            hostname=self.host,
            port=self.port
        )
        self.controller.start()
        print(f"MailTrap server running on {self.host}:{self.port}")

    def stop(self):
        if self.controller:
            self.controller.stop()


class Email(BaseModel):
    id: int
    from_address: str
    to: List[str]
    subject: str
    date: str
    content_text: Optional[str]
    content_html: Optional[str]
    attachments: List[dict]


app = FastAPI()

# Configure CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/emails")
async def get_emails():
    handler = MailTrapHandler()
    return handler.emails


@app.get("/api/emails/{email_id}")
async def get_email(email_id: int):
    handler = MailTrapHandler()
    email = next((e for e in handler.emails if e['id'] == email_id), None)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    return email


@app.delete("/api/emails/{email_id}")
async def delete_email(email_id: int):
    handler = MailTrapHandler()
    email = next((e for e in handler.emails if e['id'] == email_id), None)
    if email is None:
        raise HTTPException(status_code=404, detail="Email not found")
    
    # Remove email from storage
    handler.emails = [e for e in handler.emails if e['id'] != email_id]
    with open(handler.emails_file, 'w') as f:
        json.dump(handler.emails, f, indent=2)
    
    # Remove attachments
    for attachment in email['attachments']:
        try:
            os.remove(attachment['path'])
        except OSError:
            pass
    
    return {"message": "Email deleted"}

if __name__ == "__main__":
    import uvicorn
    
    # Start the SMTP server
    mail_trap = MailTrap()
    mail_trap.start()
    
    # Start the FastAPI server
    uvicorn.run(app, host="127.0.0.1", port=8025)
