'use strict';
/* Только интерфейс: ответы сервера и локальный список покупок.
   Каталог, цены, наличие и окончательное подтверждение остаются на сервере. */
(function(){
 const products=new Map(),selection=new Map();
 let currentProposal=null;
 const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
 function action(label,fn,cls='guide-choice'){const b=el('button',cls,label);b.type='button';b.addEventListener('click',()=>{if(!busy)fn()});return b}
 function finishStep(container){container.querySelectorAll('button,input').forEach(n=>n.disabled=true)}
 function helpCurrent(){return send('Помогите уточнить запрос по моей текущей задаче. Учтите предыдущие сообщения и объясните, каких данных ещё не хватает.');}
 function remember(data){for(const p of [...(data.products||[]),...(data.alternatives||[])])products.set(String(p.id),p);if(data.cart&&state?.cart){for(const item of data.cart.items){const before=state.cart.items.find(x=>String(x.id)===String(item.id))?.cart_quantity||0;const delta=item.cart_quantity-before;const staged=selection.get(String(item.id));if(staged&&delta>0){staged.quantity-=delta;if(staged.quantity<=0)selection.delete(String(item.id));}}renderSelection()}}
 function stage(id,amount){if(busy)return;const p=products.get(String(id));if(!p||p.city!==state.city){toast('Обновите поиск для текущего города.');return}const current=selection.get(String(id))?.quantity||0,already=state.cart.items.find(x=>String(x.id)===String(id))?.cart_quantity||0;const next=current+amount;
  if(!Number.isFinite(amount)||amount<=0||!Number.isFinite(p.minimum)||Math.abs(amount/p.minimum-Math.round(amount/p.minimum))>1e-7){toast('Количество должно соответствовать минимальной партии.');return}
  if(p.price===null||p.available===null||next+already>p.available){toast('Такого количества нет в выбранном городе с учётом корзины и списка.');return}if(!selection.has(String(id))&&selection.size>=12){toast('В одном наборе можно подтвердить до 12 разных позиций.');return}
  selection.set(String(id),{product:p,quantity:next});setStep(2,'Товар в вашем списке. Нажмите «Проверить набор», когда закончите выбор.');renderSelection();toast('Добавлено в ваш список. Корзина пока не изменилась.');}
 function renderSelection(){let total=0;for(const item of selection.values())total+=item.quantity*item.product.price;$('selection-bar').hidden=!selection.size;$('selection-count').textContent=`Позиций: ${selection.size}`;$('selection-total').textContent=money(total);const target=$('selection-items');target.replaceChildren();
  for(const [id,item]of selection){const row=el('div','selection-item');row.append(el('strong','',item.product.name),el('p','selection-detail',`${item.product.article} · ${item.product.city} · ${money(item.product.price)} / ед.`));const input=el('input','qty');input.type='number';input.min=String(item.product.minimum);input.step=String(item.product.minimum);input.max=String(item.product.available);input.value=String(item.quantity);input.setAttribute('aria-label',`Количество в списке ${item.product.article}`);input.addEventListener('change',()=>{const count=Number(input.value),inCart=state.cart.items.find(x=>String(x.id)===id)?.cart_quantity||0;if(!input.value||!input.reportValidity()||count+inCart>item.product.available){input.value=String(item.quantity);toast('Проверьте количество и остаток.');return}item.quantity=count;renderSelection()});row.append(input,action('Убрать из списка',()=>{selection.delete(id);renderSelection()},'remove-item'));target.append(row)}
  $('prepare-selection-dialog').disabled=!selection.size||busy;$('prepare-selection').disabled=!selection.size||busy;if(!selection.size)target.append(el('p','','Список пуст. Выберите товары в карточках.'));
 }
 async function prepareSelection(){if(busy||!selection.size)return;invalidateProposal();$('selection-dialog').close();await withBusy(async()=>{const data=await api('/api/chat',{action:'propose',items:[...selection.values()].map(x=>({id:x.product.id,quantity:x.quantity}))});currentProposal=data.proposal?.id;addResponse(data)})}
 function afterResponse(data,container){
  for(const line of data.kit?.items||[]){
   for(const button of container.querySelectorAll('[data-add]')){
    if(button.dataset.add===String(line.product.id))button.parentElement.querySelector('.qty').value=String(line.quantity);
   }
  }
  if(data.kit){
   const kit=el('section','kit-summary');
   kit.append(el('h3','',data.kit.complete?'Состав комплекта для указанных условий':'Комплект пока не завершён'));kit.append(el('p','guide-description','Проверьте назначение и количество каждой позиции. Полнота относится только к выбранному сценарию.'));
   if(data.kit.mode==='demo')kit.append(el('p','guide-note','Учебные товары и цены — не предложение магазина.'));
   for(const line of data.kit.items||[]){
    const item=el('div','kit-purpose');
    item.append(el('strong','',`${line.product.name} · ${quantity(line.quantity)} ед.`),el('p','',line.purpose));
    kit.append(item);
   }
   if(data.kit.checks?.length)kit.append(el('h4','','Что проверено'));
   for(const check of data.kit.checks||[]){
    kit.append(el('p','kit-check',`${check.status==='passed'?'✓':'!'} ${check.name}: ${check.detail}`));
   }
   if(data.kit.unresolved?.length)kit.append(el('h4','','Что ещё нужно уточнить'));
   for(const issue of data.kit.unresolved||[])kit.append(el('p','card-warning',issue));
   if(data.kit.notice)kit.append(el('p','guide-note',data.kit.notice));
   container.insertBefore(kit,container.querySelector('.product-grid,.proposal'));
  }
  if(data.options?.length){
   const row=el('div','guide-choices server-choices');
   for(const label of data.options)row.append(action(label,()=>{finishStep(row);send(label)}));
   const hint=el('p','guide-note','Выберите ответ ниже или напишите свой в поле сообщения.');container.append(hint,row);
  }
 }
 function reset(){currentProposal=null;products.clear();/* Список сохраняется до явного изменения пользователем. */}

 function lock(value){document.querySelectorAll('[data-start-guide],#help-current,#prepare-selection,#prepare-selection-dialog').forEach(b=>b.disabled=value||(!b.hasAttribute('data-start-guide')&&b.id!=='help-current'&&!selection.size))}
 document.querySelectorAll('[data-start-guide]').forEach(button=>button.addEventListener('click',helpCurrent));
 $('help-current').addEventListener('click',helpCurrent);
 $('show-selection').addEventListener('click',()=>{renderSelection();$('selection-dialog').showModal()});$('close-selection').addEventListener('click',()=>$('selection-dialog').close());$('prepare-selection').addEventListener('click',prepareSelection);$('prepare-selection-dialog').addEventListener('click',prepareSelection);
 window.FrontendGuide={remember,afterResponse,stage,reset,lock,ready(serverState){$('build-version').textContent='Версия '+(serverState.version||'local');$('mobile-build-version').textContent='Версия '+(serverState.version||'local')},confirmed(id){if(id===currentProposal)currentProposal=null},cityChanged(){products.clear();selection.clear();renderSelection();toast('Город изменён. Найдите товары заново: наличие зависит от склада.')}};
})();
