'use strict';
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=value=>value===null?'Цена уточняется':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:2}).format(value)+' ₸';
const quantity=value=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:3}).format(value);
const link=url=>typeof url==='string'&&(url.startsWith('https://')||url.startsWith('/static/'))?esc(url):'#';
let csrf='',state=null,busy=false,attachment=null,toastTimer=null;
let activeProposal=null;
function setStep(index,hint){
 $('flow-steps').innerHTML=ShoppingFlow.labels.map((label,i)=>`<li class="${i===index?'current':i<index?'done':''}" ${i===index?'aria-current="step"':''}><span>${i+1}</span>${esc(label)}</li>`).join('');
 $('flow-hint').textContent=hint;
}
function invalidateProposal(){activeProposal=null;document.querySelectorAll('.proposal button').forEach(b=>b.disabled=true);document.querySelectorAll('.proposal-note').forEach(n=>n.textContent='Это предложение больше не активно. Запросите проверку состава заново.');}
function refreshProposal(){
 if(activeProposal&&!ShoppingFlow.canConfirm(activeProposal,activeProposal.id)){invalidateProposal();setStep(3,'Время подтверждения истекло. Проверьте набор заново.');}
}
setInterval(refreshProposal,1000);
setStep(0,'Опишите, что хотите сделать. Названия товаров знать не обязательно.');
async function api(path,body,method='POST'){
 const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),50000);
 const options={method,credentials:'include',signal:controller.signal,headers:{'X-CSRF-Token':csrf}};
 if(body instanceof FormData)options.body=body;
 else if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body)}
 try { const result=await fetch(path,options);let data;try{data=await result.json()}catch{throw new Error('Сервер вернул неожиданный ответ. Попробуйте ещё раз.')}
 if(!result.ok){if(data.cart)updateCart(data.cart);if(result.status===409)invalidateProposal();if(result.status===403){const session=await fetch('/api/cart',{credentials:'include'});if(session.ok){const fresh=await session.json();csrf=fresh.csrf;updateCart(fresh);invalidateProposal();}}throw new Error(typeof data.detail==='string'?data.detail:'Не удалось выполнить запрос. Проверьте данные.')}
 return data; } catch(error){if(error.name==='AbortError')throw new Error('Ответ задерживается. Перед повторным подтверждением проверьте корзину.');throw error}finally{clearTimeout(timeout)}
}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,6500)}
function scrollBottom(){$('conversation').scrollTop=$('conversation').scrollHeight}
function revealChat(){$('welcome').hidden=true;document.querySelectorAll('.side-prompt,.second-label').forEach(n=>n.hidden=true)}
function addUser(text){revealChat();const el=document.createElement('div');el.className='message user';el.innerHTML=`<div class="user-bubble">${esc(text)}</div>`;$('messages').append(el);scrollBottom()}
function card(p){
 const attrs=Object.entries(p.attributes||{}).slice(0,6);
 const available=p.available;const valid=available!==null&&available>0&&p.price!==null;
 return `<article class="product-card"><div class="product-top">${p.image?`<img class="product-picture" src="${link(p.image)}" alt="${esc(p.name)}" loading="lazy">`:'<div class="product-picture" aria-hidden="true">ϟ</div>'}<div class="product-info"><div class="sku">АРТ. ${esc(p.article)}</div><h3>${esc(p.name)}</h3></div></div><div class="product-attributes">${attrs.map(([k,v])=>`<div class="attribute-row"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('')}</div><div class="stock-line ${!valid?'empty':''}"><i></i>${available===null?'Наличие в городе не подтверждено':available>0?`${esc(p.city)} · ${quantity(available)} в наличии`:`${esc(p.city)} · нет в наличии`}</div>${(p.warnings||[]).map(w=>`<div class="card-warning">${esc(w)}</div>`).join('')}${p.reason?`<div class="card-reason">${esc(p.reason)}</div>`:''}<div class="card-links">${p.url?`<a href="${link(p.url)}" target="_blank" rel="noopener noreferrer">Карточка EKT ↗</a>`:''}${(p.certificates||[]).length?(p.certificates||[]).map(c=>`<a href="${link(c.url)}" target="_blank" rel="noopener noreferrer">${esc(c.title)} ↗</a>`).join(''):'<span class="certificate-missing">Сертификат не указан</span>'}</div><div class="product-footer"><div class="price">${money(p.price)}<small>${p.mode==='demo'?'Учебные данные':'Цена из каталога'} · партия ${quantity(p.minimum)}</small></div><div class="product-add"><input class="qty" type="number" min="${p.minimum}" step="${p.minimum}" max="${available??0}" value="${p.minimum}" aria-label="Количество ${esc(p.article)}" ${!valid?'disabled':''}><button class="add-button" data-add="${esc(p.id)}" ${!valid?'disabled':''}>В список +</button></div></div></article>`;
}
function proposalMarkup(p){
 const add=p.operation==='add';const heading=add?'Проверьте состав перед добавлением':p.operation==='clear'?'Очистить корзину?':'Удалить товар из корзины?';
 return `<section class="proposal"><h3>${heading}</h3><div class="proposal-city">${esc(p.city)} · демонстрационная корзина</div>${p.items.map(x=>`<div class="proposal-line"><span>${esc(x.name)}</span><span>${quantity(x.requested_quantity)} × ${money(x.price)}</span></div>`).join('')}<div class="proposal-total"><span>${add?'К добавлению':'Сумма позиций'}</span><span>${money(p.total)}</span></div><div class="proposal-buttons"><button class="primary-button" data-confirm="${esc(p.id)}">${add?'Подтвердить и добавить в корзину':'Подтвердить изменение корзины'}</button><button class="secondary-button" data-cancel>Отмена</button></div><p class="proposal-note">${add?'Перед добавлением ещё раз проверим цену и остаток. ':''}Нажмите кнопку только после проверки состава. Текст «да» не добавляет товары. Предложение действует 5 минут.</p></section>`;
}
function addResponse(data){
 invalidateProposal();activeProposal=data.proposal||null;
 const hints=['Продолжайте консультацию: напишите сообщение или воспользуйтесь помощью ниже.','Ответьте на вопрос ниже. Можно написать своими словами.','Сравните варианты и соберите свой список. Он ещё не в корзине.','Проверьте состав, количество и нерешённые вопросы перед подтверждением.','','Изменение корзины подтверждено сервером. Откройте её для проверки.'];
 setStep(ShoppingFlow.step(data),hints[ShoppingFlow.step(data)]);
 window.FrontendGuide?.remember(data);
 revealChat();document.querySelectorAll('.proposal button,.server-choices button').forEach(b=>b.disabled=true);
 const el=document.createElement('div');el.className='message assistant';
 el.innerHTML=`<div class="assistant-label"><span class="assistant-avatar">✧</span>EKT Ассистент</div><div class="message-text">${esc(data.message)}</div>${data.engine==='fallback'?'<div class="engine-note">AI временно недоступен. Использован базовый поиск по каталогу.</div>':''}${data.products?.length?`<div class="product-grid">${data.products.map(card).join('')}</div>`:''}${data.alternatives?.length?`<div class="section-caption">Подтверждённые альтернативы</div><div class="product-grid">${data.alternatives.map(card).join('')}</div>`:''}${data.sources?.length?`<div class="sources">${data.sources.map(s=>`<a href="${link(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`).join('')}</div>`:''}${data.proposal?proposalMarkup(data.proposal):''}${data.cart?.items?.length&&!data.proposal?'<a href="/cart" class="cart-link" data-cart>Открыть актуальную корзину ↗</a>':''}`;
 $('messages').append(el);window.FrontendGuide?.afterResponse(data,el);if(data.cart)updateCart(data.cart);if(data.city)$('city').value=data.city;scrollBottom();
}
function addError(error){revealChat();const e=document.createElement('div');e.className='message error-message';e.textContent=error.message||String(error);e.setAttribute('role','alert');const note=document.createElement('p');note.textContent='Запрос не повторяется автоматически. При ошибке подтверждения сначала откройте корзину и проверьте её состав.';e.append(note);$('messages').append(e);scrollBottom()}
function updateCart(cart){if(state)state.cart=cart;$('cart-count').textContent=quantity(cart.count);$('cart-total').textContent=money(cart.total);$('clear-cart').disabled=!cart.items.length;$('cart-items').innerHTML=cart.items.length?cart.items.map(p=>`<div class="cart-line"><div class="sku">${esc(p.article)} · ${esc(p.city)}</div><h3>${esc(p.name)}</h3><div class="cart-line-details"><span>${quantity(p.cart_quantity)} × ${money(p.price)}</span><strong>${money(p.total)}</strong></div><button class="remove-item" data-remove="${esc(p.id)}">Удалить позицию</button></div>`).join(''):'<div class="empty-cart"><span>▱</span>Здесь появятся выбранные товары.<br>Найдите товар в чате и подтвердите добавление.</div>'}
async function openCart(){try{updateCart(await api('/api/cart',undefined,'GET'));$('drawer-backdrop').hidden=false;$('cart-drawer').hidden=false;document.querySelector('.workspace').inert=true;document.querySelector('.sidebar').inert=true;$('close-cart').focus()}catch(e){toast('Не удалось обновить корзину: '+e.message)}}
function closeCart(){if($('cart-drawer').hidden)return;document.querySelector('.workspace').inert=false;document.querySelector('.sidebar').inert=false;$('drawer-backdrop').hidden=true;$('cart-drawer').hidden=true;$('open-cart').focus()}
async function withBusy(fn){if(busy)return;if(!csrf){toast('Сначала дождитесь подключения к серверу.');return;}busy=true;window.FrontendGuide?.lock(true);$('city').disabled=true;$('send-button').disabled=true;const thinking=document.createElement('div');thinking.className='thinking';thinking.setAttribute('role','status');$('conversation').setAttribute('aria-busy','true');thinking.innerHTML='<span>Проверяю данные каталога…</span>';$('messages').append(thinking);revealChat();scrollBottom();try{await fn()}catch(e){addError(e)}finally{thinking.remove();$('conversation').setAttribute('aria-busy','false');busy=false;window.FrontendGuide?.lock(false);$('city').disabled=false;$('send-button').disabled=false;scrollBottom()}}
async function send(message){
 if(!message.trim()||busy)return;
 if(!csrf){toast('Дождитесь подключения к серверу.');return;}
 invalidateProposal();
 document.querySelectorAll('.server-choices button').forEach(b=>b.disabled=true);
 const current=attachment;
 addUser(message+(current?`\n📎 ${current.name}`:''));
 $('message-input').value='';$('message-input').style.height='auto';attachment=null;renderAttachment();
 await withBusy(async()=>{try{
  addResponse(await api('/api/chat',{message,attachment_id:current?.attachment_id||null}));
 }catch(e){$('message-input').value=message;attachment=current;renderAttachment();throw e}});
 $('message-input').focus();
}
function renderAttachment(){$('attachment-pill').hidden=!attachment;if(attachment)$('attachment-pill').innerHTML=`${esc(attachment.name)}<button id="remove-attachment" aria-label="Убрать вложение">×</button>`}
$('chat-form').addEventListener('submit',e=>{e.preventDefault();send($('message-input').value|| (attachment?'Подбери товары по вложению':''))});
$('message-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('chat-form').requestSubmit()}});
$('message-input').addEventListener('input',e=>{e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,120)+'px'});
$('open-cart').onclick=openCart;$('close-cart').onclick=closeCart;$('drawer-backdrop').onclick=closeCart;
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeCart();if(e.key==='Tab'&&!$('cart-drawer').hidden){const nodes=[...$('cart-drawer').querySelectorAll('button:not(:disabled),a[href]')];const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}});
$('new-chat').onclick=async()=>{if(busy)return;try{await api('/api/chat',{action:'reset'});invalidateProposal();setStep(0,'Начните новую задачу. Корзина и список сохранены.');window.FrontendGuide?.reset();$('messages').replaceChildren();attachment=null;renderAttachment();$('welcome').hidden=false;document.querySelectorAll('.side-prompt,.second-label').forEach(n=>n.hidden=false);$('message-input').focus()}catch(e){toast(e.message)}};
document.addEventListener('click',e=>{
 const prompt=e.target.closest('[data-prompt]');if(prompt){send(prompt.dataset.prompt);return}
 const suggestion=e.target.closest('[data-suggestion]');if(suggestion){const demo=state?.catalog.mode==='demo';send(suggestion.dataset.suggestion==='stock'?(demo?'Есть DEMO-C16-A?':'Есть 200300285_?'):(demo?'Подбери аналог DEMO-C16-Z':'Подбери аналог ярп4520'));return}
 const add=e.target.closest('[data-add]');if(add){const field=add.parentElement.querySelector('.qty');if(!field.reportValidity()||!field.value)return;const q=Number(field.value);if(window.FrontendGuide){window.FrontendGuide.stage(add.dataset.add,q);return;}withBusy(async()=>addResponse(await api('/api/chat',{action:'propose',items:[{id:add.dataset.add,quantity:q}]})));return}
 const confirm=e.target.closest('[data-confirm]');if(confirm){if(busy||!ShoppingFlow.canConfirm(activeProposal,confirm.dataset.confirm)){refreshProposal();return;}confirm.disabled=true;setStep(4,'Отправляем подтверждение. Не закрывайте страницу — проверяем результат.');withBusy(async()=>{const data=await api('/api/cart/confirm',{proposal_id:confirm.dataset.confirm,confirmed:true});window.FrontendGuide?.confirmed(confirm.dataset.confirm);addResponse(data)});return}
 if(e.target.closest('[data-cancel]')){withBusy(async()=>addResponse(await api('/api/chat',{action:'cancel'})));return}
 const remove=e.target.closest('[data-remove]');if(remove){closeCart();withBusy(async()=>addResponse(await api('/api/chat',{action:'propose',operation:'remove',items:[{id:remove.dataset.remove,quantity:1}]})));return}
 if(e.target.closest('[data-cart]')){e.preventDefault();openCart();return}
 if(e.target.closest('#remove-attachment')){attachment=null;renderAttachment()}
});
$('clear-cart').onclick=()=>{closeCart();withBusy(async()=>addResponse(await api('/api/chat',{action:'propose',operation:'clear',items:[]})))};
$('city').onchange=async()=>{try{const r=await api('/api/city',{city:$('city').value});state.city=r.city;window.FrontendGuide?.cityChanged();updateCart(r.cart);document.querySelectorAll('.proposal button').forEach(b=>b.disabled=true);toast(r.message)}catch(e){$('city').value=state.city;toast(e.message)}};
$('attach-button').onclick=$('upload-tile').onclick=()=>$('file-input').click();
$('file-input').onchange=async()=>{const file=$('file-input').files[0];if(!file)return;if(file.size>8*1024*1024){toast('Максимальный размер файла — 8 МБ.');return}const data=new FormData();data.append('file',file);$('attach-button').disabled=true;try{attachment=await api('/api/upload',data);renderAttachment();$('message-input').focus();toast('Файл готов. Напишите запрос или нажмите отправить.')}catch(e){toast(e.message)}finally{$('attach-button').disabled=false;$('file-input').value=''}};
async function loadState(){try{const initial=await api('/api/cart',undefined,'GET');csrf=initial.csrf;state=await api('/api/state',undefined,'GET');csrf=state.csrf;$('city').value=state.city;updateCart(state.cart);$('catalog-status').textContent=state.catalog.loading?'Загружаем каталог…':state.catalog.mode==='demo'?'Учебный каталог · '+state.catalog.count+' товаров':'Каталог EKT · '+state.catalog.count+' товаров в выборке';$('ai-status').textContent=state.ai_enabled?'AI подключён':'Базовый поиск';$('status-dot').classList.toggle('warn',!!state.catalog.error||state.catalog.mode==='demo');$('setup-link').hidden=!state.setup_allowed||state.ai_enabled;$('stock-example').textContent=state.catalog.mode==='demo'?'Например, DEMO-C16-A':'Например, 200300285_';if(state.catalog.error)toast(state.catalog.error);window.FrontendGuide?.ready(state);if(state.proposal&&!activeProposal&&!$('messages').children.length)addResponse({message:'У вас осталось неподтверждённое предложение. Проверьте состав.',proposal:state.proposal,cart:state.cart});if(state.catalog.loading)setTimeout(loadState,2500)}catch(e){$('catalog-status').textContent='Нет связи с сервером';$('ai-status').textContent='Запросы недоступны';toast('Нет связи с приложением. Обновите страницу.')}}
loadState().then(()=>{if(location.pathname==='/cart')openCart()});
