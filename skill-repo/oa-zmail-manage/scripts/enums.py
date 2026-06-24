#!/usr/bin/env python3
from enum import Enum


class MailboxId(str, Enum):
    INBOX = "1"
    DRAFT = "2"
    OUTBOX = "3"
    DELETED = "4"
    SPAM = "5"

