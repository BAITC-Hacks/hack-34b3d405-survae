const {test}=require('node:test');
const assert=require('node:assert/strict');
const flow=require('../static/flow.js');
test('clarifications remain visible with AI enabled and unresolved kit',()=>{
 assert.equal(flow.step({engine:'openai',options:['2 м'],kit:{complete:false,unresolved:['Длина']}}),1);
});
test('review is distinct from actual cart update',()=>{
 assert.equal(flow.step({proposal:{id:'p'},cart:{count:0}}),3);
 assert.equal(flow.step({stage:'cart_updated',cart:{count:3}}),5);
 assert.notEqual(flow.step({message:'да'}),5);
});
test('old, expired and malformed proposals cannot be confirmed',()=>{
 const p={id:'p',expires_at:200};
 assert.equal(flow.canConfirm(p,'p',199000),true);
 assert.equal(flow.canConfirm(p,'p',200000),false);
 assert.equal(flow.canConfirm(p,'other',100000),false);
 assert.equal(flow.canConfirm({...p,expires_at:null},'p',0),false);
 assert.equal(flow.canConfirm(null,'p'),false);
});
