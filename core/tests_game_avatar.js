const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('core/templates/core/_game_avatar.html','utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
let time = 0;
const scope = {performance:{now:()=>time}, escapeHtml:value=>String(value).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;'),
  document:{getElementById:()=>({textContent:JSON.stringify({shapes:{square:{anchors:{head:{point:{x:.5,y:0},span:1,rotation:0}}}}})})}};
vm.createContext(scope);vm.runInContext(script,scope);
function frame(key){
  const avatar={dataset:{motionKey:key},style:{values:{},setProperty(name,value){this.values[name]=value;}}};
  scope.animateGameAvatars({querySelectorAll:()=>[avatar]});
  return avatar.style.values;
}
const first=frame('student-12');
const period=parseFloat(first['--gaze-period']);
assert.ok(period>=5400 && period<=6000);
// Replace the avatar repeatedly faster than the old per-node gaze timeout.
for(time=200;time<10000;time+=200){
  const next=frame('student-12');
  const phase=(parseFloat(next['--gaze-delay'])-parseFloat(first['--gaze-delay'])+time)%period;
  assert.ok(Math.abs(phase)<.001 || Math.abs(phase-period)<.001);
  assert.equal(next['--blink-period'],first['--blink-period']);
}
assert.notEqual(frame('student-13')['--gaze-delay'],frame('student-12')['--gaze-delay']);
const html=scope.gameParticipantLabelHtml({student_id:12,student_name:'<img src=x>',avatar_shape:'square',avatar_color:'#123456',avatar_accessories:{head:'cap',_set_items:[{layer:'front',url:'https://assets.example/hat.png',width:128,height:128,matrices:{square:'matrix(1 0 0 1 0 0)'}}]}});
assert.ok(html.includes('gameAvatarPortrait') && html.includes('gameAvatarLegacyHead') && html.includes('tower-set-attachments'));
assert.ok(html.includes('&lt;img src=x>') && !html.includes('<img src=x>'));
assert.ok(!scope.gameParticipantLabelHtml(null,'Waiting').includes('gameAvatarPortrait'));
console.log('Avatar motion continuity, independent phases, equipment and escaped identities: OK');
