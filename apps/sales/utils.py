import io
import os

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.http import HttpResponse
from xhtml2pdf import pisa


def link_callback(uri, rel):
    """Resolve {% static %} / {% media %} URIs to absolute filesystem paths so
    xhtml2pdf can embed images (logo, stamp) in the generated PDF.

    Falls back to returning the URI unchanged for http(s) or unresolved paths.
    """
    if uri.startswith(('http://', 'https://', 'data:')):
        return uri

    media_url = settings.MEDIA_URL or '/media/'
    static_url = settings.STATIC_URL or '/static/'

    path = None
    if media_url and uri.startswith(media_url):
        path = os.path.join(settings.MEDIA_ROOT, uri[len(media_url):])
    elif static_url and uri.startswith(static_url):
        rel_path = uri[len(static_url):]
        found = finders.find(rel_path)          # dev: locate in app static dirs
        if found:
            path = found if isinstance(found, str) else found[0]
        elif settings.STATIC_ROOT:              # prod: collected static
            path = os.path.join(settings.STATIC_ROOT, rel_path)
    else:
        return uri

    if path and os.path.isfile(path):
        return path
    return uri


def render_to_pdf(template_src, context_dict={}):
    template = get_template(template_src)
    html = template.render(context_dict)
    result = io.BytesIO()
    pdf = pisa.pisaDocument(
        io.BytesIO(html.encode("UTF-8")), result, link_callback=link_callback
    )
    if not pdf.err:
        return HttpResponse(result.getvalue(), content_type='application/pdf')
    return None
