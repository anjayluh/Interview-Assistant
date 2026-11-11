"""Extract reviewable text from a bounded local upload."""
from io import BytesIO
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader

from .profile import MAX_FACTS

MAX_UPLOAD = 5 * 1024 * 1024
MAX_XML = 3 * 1024 * 1024
MAX_PAGES = 20
WORD_NS = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def extract_cv(content, extension):
    if not content or len(content) > MAX_UPLOAD:
        raise ValueError('Choose a PDF or DOCX file up to 5 MiB.')
    try:
        if extension == 'pdf' and content.startswith(b'%PDF-'):
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise ValueError('Use an unlocked PDF.')
            if len(reader.pages) > MAX_PAGES:
                raise ValueError('Use a PDF with no more than 20 pages.')
            parts = []
            total = 0
            for page in reader.pages:
                text = page.extract_text() or ''
                parts.append(text[:MAX_FACTS - total])
                total += len(text)
                if total >= MAX_FACTS:
                    break
        elif extension == 'docx' and content.startswith(b'PK'):
            with ZipFile(BytesIO(content)) as archive:
                member = archive.getinfo('word/document.xml')
                if member.file_size > MAX_XML:
                    raise ValueError('The DOCX document content is too large.')
                xml = archive.read(member)
            # Reject entities/DTDs and non-UTF-8 encodings before parsing.
            xml_text = xml.decode('utf-8-sig')
            if re.search(r'<!\s*(DOCTYPE|ENTITY)', xml_text, re.I):
                raise ValueError('Unsupported DOCX XML declarations.')
            document = ElementTree.fromstring(xml_text)
            parts = [''.join(run.text or '' for run in paragraph.iter(WORD_NS + 't'))
                     for paragraph in document.iter(WORD_NS + 'p')]
        else:
            raise ValueError('Choose a valid PDF or DOCX file.')
    except (BadZipFile, KeyError, ElementTree.ParseError, UnicodeError):
        raise ValueError('The CV file could not be read.') from None
    except ValueError:
        raise
    except Exception:
        raise ValueError('The CV file could not be read.') from None
    text = '\n'.join(part.strip() for part in parts if part.strip()).strip()
    if not text:
        raise ValueError('No readable text found. Use a text-based PDF or DOCX.')
    return text[:MAX_FACTS]
