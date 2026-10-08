import os
import tempfile
import re
import subprocess
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

os.environ['DJANGO_SETTINGS_MODULE'] = 'progaplatform.settings'
os.environ['DEBUG'] = 'True'
import django
from django.conf import settings

temp = tempfile.TemporaryDirectory(prefix='lesson-review-')
settings.DATABASES['default']['NAME'] = str(Path(temp.name) / 'review.sqlite3')
settings.ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']
settings.CSRF_TRUSTED_ORIGINS = ['http://localhost:8767', 'http://127.0.0.1:8767']
django.setup()
from django.core.management import call_command
call_command('migrate', verbosity=0)
from core.models import *
from core.security import auth_version
from django.test import Client
teacher = Teacher.objects.create(full_name='Учитель',pin_hash='!',is_active=True)
group = ClassGroup.objects.create(name='7 А',owner=teacher)
other = ClassGroup.objects.create(name='7 Б',owner=teacher)
student = Student.objects.create(full_name='Тестовый ученик',class_group=group,pin_hash='!',is_active=True)
lesson = Session.objects.create(title='Переменные и типы данных',author=teacher,status='running')
lesson.allowed_classes.add(group,other)
theory = TheoryMaterialModule.objects.create(session=lesson,position=1,title='Знакомство с переменными',topic='Python: первые шаги')
TheoryMaterialBlock.objects.create(module=theory,ordinal=1,block_type='heading',heading_level='h1',content='Как хранить данные')
TheoryMaterialBlock.objects.create(module=theory,ordinal=2,block_type='text',content='Переменная хранит значение, которое можно изменить. Имя переменной помогает понять, какие данные в ней находятся.')
TheoryMaterialBlock.objects.create(module=theory,ordinal=3,block_type='code',content='name = "Алия"\nage = 12\nprint(name, age)')
quiz = TheoryQuizModule.objects.create(session=lesson,position=2,title='Проверь себя',instructions='Ответьте на вопросы о переменных.')
q = TheoryQuizQuestion.objects.create(module=quiz,ordinal=1,question_type='single_choice',prompt='Какое значение является целым числом?')
for i,text in enumerate(['42','3.14','"Привет"'],1):
    TheoryQuizChoice.objects.create(question=q,ordinal=i,text=text,is_correct=i==1)
TheoryQuizQuestion.objects.create(module=quiz,ordinal=2,question_type='open_answer',prompt='Что хранит переменная?',model_answer='Значение')
game = GameModule.objects.create(session=lesson,position=3,title='Гонка умов',rubric='mind_race')
MindRacePrompt.objects.create(module=game,ordinal=1,sentence='Вывод выполняет print',missing_text='print')
round_ = GameRound.objects.create(module=game,class_group=group,moderator=teacher,status='lobby',run_number=1)
GameParticipant.objects.create(round=round_,student=student,avatar_color='#38bdf8',avatar_shape='circle')
tournament=GameModule.objects.create(session=lesson,position=4,title='Турнир',rubric='tournament')
from django.utils import timezone
from datetime import timedelta
exam=Exam.objects.create(owner=teacher,title='Проверочная работа')
exam.allowed_classes.add(group)
question=ExamQuestion.objects.create(exam=exam,position=1,question_type='open_text',prompt='Что такое переменная?',model_answer='Именованное значение',max_score=5)
attempt=ExamAttempt.objects.create(exam=exam,student=student,status='submitted',started_at=timezone.now(),expires_at=timezone.now()+timedelta(hours=1))
ExamAnswer.objects.create(attempt=attempt,question=question,text_answer='Переменная хранит значение')
keys={}
for role, attrs in [('teacher',{'teacher_id':teacher.id,'teacher_auth_version':auth_version('!')}),('student',{'student_id':student.id,'student_class_id':group.id,'student_auth_version':auth_version('!')})]:
    c=Client(); s=c.session;s.update(attrs);s['ui_lang']='ru';s.save();keys[role]=s.session_key
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
app=StaticFilesHandler(get_wsgi_application())
for role,paths in [('teacher',['/teacher/modules/','/teacher/sessions/','/teacher/students/','/teacher/sessions/1/lesson/','/teacher/exams/']),('student',['/student/'])]:
    client=Client();client.cookies['sessionid']=keys[role]
    for path in paths:
        response=client.get(path)
        for i,script in enumerate(re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>',response.content.decode(),re.S)):
            file=Path(temp.name)/'check.js';file.write_text(script,encoding='utf-8')
            subprocess.run(['node','--check',str(file)],check=True,capture_output=True)
print('Rendered JavaScript syntax checks passed',flush=True)
def wrapper(environ,start_response):
    from django.template import engines
    for engine in engines.all():
        for loader in engine.engine.template_loaders:
            if hasattr(loader,'reset'):loader.reset()
    role='student' if environ['HTTP_HOST'].startswith('127.0.0.1') else 'teacher'
    cookies=[v for v in environ.get('HTTP_COOKIE','').split(';') if not v.strip().startswith('sessionid=')]
    environ['HTTP_COOKIE']=';'.join(cookies+[f'sessionid={keys[role]}'])
    return app(environ,start_response)
class Server(ThreadingMixIn,WSGIServer):
    daemon_threads=True
class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):pass
print('Review ready on localhost:8767 (teacher), 127.0.0.1:8767 (student)',flush=True)
try:
    make_server('127.0.0.1',8767,wrapper,server_class=Server,handler_class=QuietHandler).serve_forever()
finally:
    from django.db import connections
    connections.close_all()
    temp.cleanup()
