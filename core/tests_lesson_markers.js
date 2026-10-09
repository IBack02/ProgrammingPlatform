// Run with: node core/tests_lesson_markers.js
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const context=vm.createContext({});
vm.runInContext(readFileSync(`${__dirname}/templates/core/_lesson_markers.html`,'utf8'),context);
const toggle=(old,selected,color='blue')=>JSON.parse(JSON.stringify(context.toggleLessonMarkers(old,selected,color)));
const range=(start,end,color='yellow',block_id=1)=>({block_id,start,end,text:'Hello world',color});
assert.deepEqual(toggle([range(0,5)],[range(0,5)]),[]);
assert.deepEqual(toggle([range(0,10)],[range(3,7)]),[range(0,3),range(7,10)]);
assert.deepEqual(toggle([range(0,3)],[range(2,7)]),[range(0,2),range(2,7,'blue')]);
assert.deepEqual(toggle([range(0,3),range(3,7,'red')],[range(1,6)]),[range(0,1),range(6,7,'red')]);
assert.deepEqual(toggle([range(0,3,'blue')],[range(3,5)]),[range(0,5,'blue')]);
assert.deepEqual(toggle([range(0,3)],[range(0,3),range(0,3,'yellow',2)]),[range(0,3,'blue'),range(0,3,'blue',2)]);
console.log('Marker toggle: 6 cases passed.');
