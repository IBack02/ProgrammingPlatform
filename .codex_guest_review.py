import os
import tempfile
import uuid
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault('DJANGO_SETTINGS_MODULE','progaplatform.settings')
from django.conf import settings
review_dir=tempfile.TemporaryDirectory(prefix='dbug-guest-review-')
settings.DATABASES['default']['NAME']=str(Path(review_dir.name)/'review.sqlite3')
settings.DATABASES['default']['CONN_MAX_AGE']=0
settings.DEBUG=True
settings.ALLOWED_HOSTS=['127.0.0.1','localhost','testserver']
settings.SESSION_COOKIE_SECURE=False
settings.CSRF_COOKIE_SECURE=False
settings.SECURE_SSL_REDIRECT=False
import django
django.setup()
from django.core.management import call_command
call_command('migrate',verbosity=0)
from django.db import connections
from django.test import Client
from core.models import Teacher, ClassGroup, Student, Exam, ExamClass
from core.exam_views import _create_question
from core.guest_views import claim_guest
from core.security import auth_version

t=Teacher.objects.create(full_name='Демо учитель',pin_hash='!')
g=ClassGroup.objects.create(name='Гости · Демо',owner=t,is_guest=True,guest_access_token=uuid.uuid4())
for i in range(1,6): Student.objects.create(full_name=f'Гость {i:02}',class_group=g,pin_hash='!')
def claim(token):
    try:
        student=claim_guest(g,token)
        return student.id if student else None
    finally:
        connections.close_all()
with ThreadPoolExecutor(max_workers=12) as pool:
    results=list(pool.map(claim,[uuid.uuid4() for _ in range(20)]))
assigned=[r for r in results if r is not None]
assert len(assigned)==5 and len(set(assigned))==5, results
Student.objects.update(guest_lease_token=None,guest_lease_expires_at=None)
nonce=uuid.uuid4()
with ThreadPoolExecutor(max_workers=8) as pool:
    repeat=list(pool.map(claim,[nonce]*8))
assert len(set(repeat))==1 and repeat[0] is not None, repeat
Student.objects.update(guest_lease_token=None,guest_lease_expires_at=None)
print('Concurrency: 20 visitors / 5 accounts, and 8 repeated clicks: PASS',flush=True)
exam=Exam.objects.create(owner=t,title='Основы информатики',topic='Алгоритмы и структуры данных',instructions='Внимательно прочитайте вопросы и выберите ответы. Ответы сохраняются автоматически.',status='running',auto_grade=True)
ExamClass.objects.create(exam=exam,class_group=g)
_create_question(exam,{'position':1,'question_type':'single_choice','prompt':'Какая структура данных использует принцип «первым пришёл — первым вышел»?','max_score':3,'choices':[{'text':'Стек','is_correct':False},{'text':'Очередь','is_correct':True},{'text':'Дерево','is_correct':False}]})
_create_question(exam,{'position':2,'question_type':'matching','prompt':'Сопоставьте понятия и определения.','model_answer':'Верные пары','max_score':4,'pairs':[{'left_text':'Алгоритм','right_text':'Последовательность действий'},{'left_text':'Переменная','right_text':'Именованная область памяти'}]})
_create_question(exam,{'position':3,'question_type':'open_text','prompt':'Приведите пример использования очереди в повседневной жизни.','model_answer':'Очередь покупателей','max_score':2})
client=Client()
session=client.session
session.update({'teacher_id':t.id,'teacher_auth_version':auth_version(t.pin_hash),'ui_lang':'ru'})
session.save()
teacher_cookie=session.session_key
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
app=StaticFilesHandler(get_wsgi_application())
from wsgiref.simple_server import make_server, WSGIRequestHandler
class Quiet(WSGIRequestHandler):
    def log_message(self,*args): pass
def review_app(environ,start_response):
    if environ['PATH_INFO']=='/review/teacher':
        start_response('302 Found',[('Location','/teacher/classes/'),('Set-Cookie',f'sessionid={teacher_cookie}; Path=/; HttpOnly; SameSite=Lax'),('Set-Cookie','ui_lang=ru; Path=/')])
        return [b'']
    if environ['PATH_INFO']=='/review/guest':
        start_response('302 Found',[('Location',f'/guest/{g.guest_access_token}/'),('Set-Cookie','sessionid=; Path=/; Max-Age=0'),('Set-Cookie','ui_lang=ru; Path=/')])
        return [b'']
    # Re-read edited templates during the review.
    from django.template import engines
    for engine in engines.all():
        for loader in engine.engine.template_loaders:
            loader.reset()
    return app(environ,start_response)
print('REVIEW http://127.0.0.1:8765/review/teacher',flush=True)
with make_server('127.0.0.1',8765,review_app,handler_class=Quiet) as server:
    server.serve_forever()
