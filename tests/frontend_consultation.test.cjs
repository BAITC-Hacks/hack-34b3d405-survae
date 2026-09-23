const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

// Small DOM adapter: run the real application handlers with recorded HTTP requests.
class Element {
 constructor(){this.children=[];this.style={};this.dataset={};this.listeners={};this.value='';this.hidden=false;this.disabled=false;this.classList={toggle(){}};}
 append(...nodes){this.children.push(...nodes);}
 insertBefore(node){this.children.push(node);}
 replaceChildren(...nodes){this.children=nodes;}
 addEventListener(event,fn){this.listeners[event]=fn;}
 setAttribute(){} focus(){} remove(){} close(){} showModal(){throw Error('Unexpected scenario dialog');}
 querySelectorAll(){return [];}
 querySelector(){return null;}
 set innerHTML(value){this.html=value;this.children=[];}
 get innerHTML(){return this.html||'';}
}
async function setup(){
 const nodes=new Map();const requests=[];const cart={count:0,total:0,items:[]};
 const element=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id)};
 const proposalButton=new Element();const oldChoice=new Element();
 let answer={message:'Уточните параметры панели',options:[],products:[],kit:null,proposal:null,cart,engine:'fallback'};
 const document={getElementById:element,createElement:()=>new Element(),addEventListener(){},querySelector:element,
  querySelectorAll:selector=>selector==='.proposal button'?[proposalButton]:selector==='.server-choices button'?[oldChoice]:[]};
 const ctx=vm.createContext({document,window:{},location:{pathname:'/'},console,Intl,FormData,AbortController,
  setTimeout:()=>1,clearTimeout(){},setInterval(){},fetch:async(url,options={})=>{
   requests.push({url,...options,body:options.body?JSON.parse(options.body):undefined});
   const data=url==='/api/cart'?{...cart,csrf:'test-csrf'}:url==='/api/state'?{cart,csrf:'test-csrf',catalog:{mode:'demo',count:15},city:'Астана',ai_enabled:true,version:'test'}:answer;
   return {ok:true,json:async()=>data};
  }});
 ctx.ShoppingFlow=require('../static/flow.js');
 for(const file of ['app.js','guide.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../static',file),'utf8'),ctx);
 await new Promise(resolve=>setImmediate(resolve));requests.length=0;
 return {ctx,nodes,requests,proposalButton,oldChoice,answer:value=>{answer={...answer,...value}}};
}
test('industrial conversation and help use the same chat transport, without reset or rewritten text',async()=>{
 const h=await setup();
 const messages=['Хочу панель оператора','Для контроля механизмов','Пищевая и химическая промышленность','  Не знаю, что выбрать\nдля этой панели  '];
 for(const m of messages)await h.ctx.send(m);
 assert.deepEqual(h.requests.map(x=>x.body.message),messages);
 await h.nodes.get('help-current').listeners.click();
 assert.equal(h.requests.length,5);
 assert.match(h.requests[4].body.message,/текущей задаче/);
 assert.ok(h.requests.every(x=>x.url==='/api/chat'&&x.credentials==='include'&&!x.body.action));
 assert.equal(h.nodes.get('messages').children.filter(n=>n.className==='message user').length,5);
 assert.equal(h.proposalButton.disabled,true);
 assert.equal(h.oldChoice.disabled,true);
 assert.ok(h.nodes.get('messages').children.some(n=>n.innerHTML.includes('AI временно недоступен')));
});
test('empty options add no scenario menu; supplied options forward their exact label',async()=>{
 const h=await setup();const blank=new Element();
 h.ctx.window.FrontendGuide.afterResponse({options:[],products:[]},blank);
 assert.equal(blank.children.length,0);
 const filled=new Element();
 h.ctx.window.FrontendGuide.afterResponse({options:['Панель с сенсорным экраном'],products:[]},filled);
 const row=filled.children.find(x=>x.className==='guide-choices server-choices');
 assert.equal(row.children.length,1);
 await row.children[0].listeners.click();await new Promise(resolve=>setImmediate(resolve));
 assert.equal(h.requests.at(-1).body.message,'Панель с сенсорным экраном');
});
test('kit facts render only from the current response; history is retained and never copied into a new result',async()=>{
 const h=await setup();const kit=new Element();
 h.ctx.window.FrontendGuide.afterResponse({kit:{complete:false,mode:'demo',items:[{product:{id:'x',name:'Компонент'},quantity:2,purpose:'Назначение из API'}],checks:[{name:'Проверка',status:'passed',detail:'Факт из API'}],unresolved:['Нужны параметры']}},kit);
 const text=JSON.stringify(kit.children);
 assert.match(text,/Компонент · 2 ед/);assert.match(text,/Факт из API/);assert.match(text,/Нужны параметры/);
 const next=new Element();h.ctx.window.FrontendGuide.afterResponse({message:'Новая тема',options:[],kit:null},next);
 assert.equal(next.children.length,0);assert.equal(kit.children.length,1);
});
