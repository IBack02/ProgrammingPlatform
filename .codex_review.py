import os, tempfile, re, subprocess
from pathlib import Path
os.environ['DJANGO_SETTINGS_MODULE']='progaplatform.settings'
os.environ['DEBUG']='True'
from django.conf import settings
review_dir=Path(tempfile.mkdtemp(prefix='lesson-review-'))
settings.DATABASES['default']['NAME']=str(review_dir/'review.sqlite3')
settings.ALLOWED_HOSTS=['127.0.0.1','localhost','testserver']
import django
django.setup()
from django.core.management import call_command
call_command('migrate',verbosity=0)
from django.test import Client
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from core.models import *
from core.security import auth_version
teacher=Teacher.objects.create(full_name='Review Teacher',pin_hash=make_password('1357'))
group=ClassGroup.objects.create(name='8А',owner=teacher)
lesson=Session.objects.create(title='Интерактивный урок',author=teacher,status='running')
lesson.allowed_classes.add(group)
theory=TheoryMaterialModule.objects.create(session=lesson,position=1,title='Теория: алгоритмы')
TheoryMaterialBlock.objects.create(module=theory,ordinal=1,block_type='heading',content='Алгоритм и его свойства')
TheoryMaterialBlock.objects.create(module=theory,ordinal=2,block_type='text',content='Алгоритм — это последовательность действий для решения задачи. Каждый шаг должен быть понятным исполнителю.')
names=['Алина','Борис','Виктор','Дарья','Елена','Жанна','Иван','Карина','Лев','Мария','Никита','Ольга','Павел','Руслан','София','Тимур']
students=[Student.objects.create(full_name=n,class_group=group,pin_hash=make_password('2468')) for n in names]
race=GameModule.objects.create(session=lesson,position=2,title='Гонка умов',rubric='mind_race')
raceround=GameRound.objects.create(module=race,class_group=group,moderator=teacher,run_number=1,status='finished',total_prompts=5,finished_at=timezone.now())
for i,s in enumerate(students[:5]):
 GameParticipant.objects.create(round=raceround,student=s,progress=5 if i<3 else 4,finish_place=i+1,avatar_shape='square',avatar_color='#38bdf8')
tournament=GameModule.objects.create(session=lesson,position=3,title='Турнир',rubric='tournament')
for i in range(1,5):
 stage=TournamentStage.objects.create(module=tournament,ordinal=i,title=f'Раунд {i}',question_count=1)
 TournamentQuestion.objects.create(stage=stage,ordinal=1,prompt=f'Сколько будет {i} + {i}?',answer=str(i*2))
tr=GameRound.objects.create(module=tournament,class_group=group,moderator=teacher,run_number=1,status='lobby')
for s in students: GameParticipant.objects.create(round=tr,student=s,avatar_shape='square',avatar_color='#38bdf8')
clients={}
for who,model in [('teacher',teacher),('student',students[0]),('opponent',students[1])]:
 c=Client();session=c.session
 session[f'{"teacher" if who=="teacher" else "student"}_id']=model.id
 session[f'{"teacher" if who=="teacher" else "student"}_auth_version']=auth_version(model.pin_hash)
 if who!='teacher':session['student_class_id']=group.id
 session.save();clients[who]=c
# Compile the actual rendered scripts, including all template fragments.
for who,path in [('teacher',f'/teacher/sessions/{lesson.id}/lesson/'),('student','/student/')]:
 response=clients[who].get(path)
 assert response.status_code==200,(path,response.status_code)
 for i,script in enumerate(re.findall(r'<script\b[^>]*>(.*?)</script>',response.content.decode(),re.S)):
  if not script.strip():continue
  source=review_dir/f'{who}-{i}.js';source.write_text(script,encoding='utf-8')
  subprocess.run(['node','--check',str(source)],check=True)
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
from wsgiref.simple_server import make_server,WSGIRequestHandler
app=StaticFilesHandler(get_wsgi_application())
def review_app(environ,start_response):
 path=environ['PATH_INFO']
 if path.startswith('/review/'):
  who=path.rsplit('/',1)[-1]
  if who in clients:
   location=f'/teacher/sessions/{lesson.id}/lesson/' if who=='teacher' else '/student/'
   cookie=clients[who].cookies[settings.SESSION_COOKIE_NAME].value
   start_response('302 Found',[('Location',location),('Set-Cookie',f'{settings.SESSION_COOKIE_NAME}={cookie}; Path=/; HttpOnly')]);return [b'']
 return app(environ,start_response)
class Quiet(WSGIRequestHandler):
 def log_message(self,*args):pass
print(f'READY http://127.0.0.1:8765/review/teacher; round={tr.id}; temp={review_dir}',flush=True)
make_server('127.0.0.1',8765,review_app,handler_class=Quiet).serve_forever()

