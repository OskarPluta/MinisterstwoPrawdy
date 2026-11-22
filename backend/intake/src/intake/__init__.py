"""Intake: turns what a person sends (links, YouTube videos, images, PDFs) into rag Attachments.

Nothing here decides a verdict. Every failure becomes a stated limitation on the Attachment,
never an exception for the caller and never a result about the content.
"""

from .core import Intake, Upload, make_intake

__all__ = ["Intake", "Upload", "make_intake"]
