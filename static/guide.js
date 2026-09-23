'use strict';
/* Только интерфейс: уточнение запроса, отбор по полученным полям и локальный список.
   Каталог, цены, наличие и окончательное подтверждение остаются на сервере. */
(function(){
 const normalize=value=>String(value??'').toLowerCase().replace(/ё/g,'е').replace(/\s+/g,' ').trim();
 function matchesLamp(product,answers){
  const attrs=product.attributes||{};
  const find=part=>Object.entries(attrs).find(([key])=>normalize(key).includes(part))?.[1];
  const base=normalize(find('цоколь')).replace(/\s/g,'');
  const watts=Number.parseFloat(String(find('мощность')??'').replace(',','.'));
  const temperature=String(find('температур')??'').replace(/\s/g,'');
  return base===normalize(answers.base)&&Number.isFinite(watts)&&watts>0&&watts<=answers.maxWatts&&(!answers.temperature||temperature.includes(String(answers.temperature)));
 }
 if(typeof module!=='undefined')module.exports={matchesLamp};
 if(typeof document==='undefined')return;
 const products=new Map(),selection=new Map();
 let journey=null,version=0,currentProposal=null;
 const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
 function action(label,fn,cls='guide-choice'){const b=el('button',cls,label);b.type='button';b.addEventListener('click',()=>{if(!busy)fn()});return b}
 function help(text){const n=el('article','message assistant guide-message');const title=el('div','assistant-label','ПОМОЩНИК ПО ВЫБОРУ');n.append(title,el('p','message-text',text));revealChat();$('messages').append(n);scrollBottom();return n}
 function finishStep(container){container.querySelectorAll('button,input').forEach(n=>n.disabled=true)}
 async function startKitchen(container){
  finishStep(container);journey=null;
  await withBusy(async()=>{
   await api('/api/chat',{action:'reset'});dialogContext=null;
   addUser('Хочу подсветку кухни');
   addResponse(await api('/api/chat',{message:'Хочу подсветку кухни'}));
  });
 }
 function step(title,description,choices,number=1){
  setStep(1,'Ответьте на вопрос ниже. Неизвестные параметры не будем угадывать.');const session=version,container=help('');container.append(el('div','guide-progress',`ПОДБОР ЛАМПЫ · ШАГ ${number} ИЗ 4`),el('h2','guide-title',title),el('p','guide-description',description));
  const row=el('div','guide-choices');choices.forEach(item=>row.append(action(item.label,()=>{if(session!==version){toast('Этот подбор уже завершён. Начните новый.');return}finishStep(container);addUser(item.label);item.next()})));container.append(row);scrollBottom();return container;
 }
 function start(){if(busy)return;invalidateProposal();setStep(0,'Выберите задачу. Затем уточним условия и проверим состав.');if(!csrf){toast('Дождитесь подключения к серверу.');return;}version++;journey={};const n=help('Названия знать не обязательно. Выберите, что хотите сделать, или напишите задачу своими словами.');const row=el('div','guide-start-options');
  if(state?.ai_enabled)row.append(action('Обсудить задачу с AI',()=>{finishStep(n);withBusy(async()=>{await api('/api/chat',{action:'reset'});dialogContext=null;addUser('Помоги выбрать товары для моей задачи');addResponse(await api('/api/chat',{message:'Помоги выбрать товары. Я не знаю, что мне нужно. Спроси меня о моей задаче.'}));})}));
  row.append(action('✦ Собрать подсветку кухни',()=>startKitchen(n)));
row.append(action('💡 Заменить лампочку',()=>{finishStep(n);withBusy(async()=>{await api('/api/chat',{action:'reset'});dialogContext=null;journey={};addUser('Хочу заменить лампочку');baseStep()})}),action('⌕ Найти по маркировке или фото',()=>{finishStep(n);identify()}),action('▤ Собрать товары по списку',()=>{finishStep(n);const a=help('Вставьте список с количеством в поле сообщения или прикрепите спецификацию. Добавляйте найденные позиции в «Ваш список», затем подтвердите весь набор одним действием.');a.append(action('Прикрепить спецификацию',()=>$('file-input').click()));$('message-input').focus()}),action('✎ У меня другая задача',()=>{finishStep(n);const a=help('Опишите, что должно получиться и что уже есть. Например: «Нужен свет для рабочего стола, светильник уже есть». Можно приложить маркировку или документ.');a.append(action('Написать задачу',()=>{$('message-input').focus()}));$('message-input').focus()}));n.append(row);scrollBottom();
 }
 function identify(){const n=help('Найдите надпись на упаковке или доступной маркировке товара. Артикул можно ввести даже без названия. Не открывайте электрощит и не разбирайте подключённые приборы ради фото.');n.append(action('Ввести маркировку',()=>{$('message-input').placeholder='Перепишите артикул или маркировку…';$('message-input').focus()}),action(state?.ai_enabled?'Прикрепить фото маркировки':'Прикрепить документ',()=>$('file-input').click()));if(!state?.ai_enabled)n.append(el('p','guide-note','Распознавание фото сейчас недоступно. Перепишите видимый текст или загрузите текстовый документ.'));scrollBottom()}
 function baseStep(){step('Какой цоколь у лампы?','Цоколь — часть, которой лампа соединяется со светильником. Нужна маркировка, а не догадка по внешнему виду.',[{label:'E27 — резьбовой',next:()=>{journey.base='E27';powerStep()}},{label:'E14 — узкий резьбовой',next:()=>{journey.base='E14';powerStep()}},{label:'GU10 — два контакта',next:()=>{journey.base='GU10';powerStep()}},{label:'Не знаю / другой',next:()=>{identify();const n=help('Когда найдёте маркировку, вернитесь к вопросу. Без неё нельзя подтвердить, что лампа подойдёт.');n.append(action('Я нашёл маркировку',baseStep));scrollBottom()}}]);}
 function powerStep(){const session=version,n=step('Какая мощность разрешена?','Введите максимальную мощность лампы, указанную на светильнике. Это ограничение совместимости, не рекомендация яркости.',[],2);const label=el('label','guide-input-label','Максимальная мощность, Вт'),input=el('input','guide-number');input.type='number';input.min='1';input.max='1000';input.step='1';input.placeholder='Например, 40';input.setAttribute('aria-label','Максимальная мощность, Вт');label.append(input);n.append(label);const row=el('div','guide-choices');row.append(action('Продолжить',()=>{if(session!==version)return;if(!input.value||!input.reportValidity()){input.focus();return}journey.maxWatts=Number(input.value);finishStep(n);addUser(`На светильнике указано: до ${input.value} Вт`);lightStep()},'primary-button'),action('Не знаю, где посмотреть',()=>{finishStep(n);identify();const next=help('Поищите надпись «MAX … W» в доступной инструкции или на упаковке. Если данных нет, пока не считаем совместимость подтверждённой.');next.append(action('Нашёл допустимую мощность',powerStep));scrollBottom()}));n.append(row);scrollBottom();input.focus()}
 function lightStep(){step('Какой свет вам нравится?','Цвет света — ваше предпочтение. Если не уверены, можно сравнить варианты.',[{label:'Тёплый · 3000 K',next:()=>{journey.temperature=3000;quantityStep()}},{label:'Нейтральный · 4000 K',next:()=>{journey.temperature=4000;quantityStep()}},{label:'Не знаю — покажите варианты',next:()=>{journey.temperature=null;quantityStep()}}],3)}
 function quantityStep(){const session=version,n=step('Сколько ламп нужно заменить?','Укажите количество. В результатах покажем цену и наличие именно в выбранном городе.',[],4);const label=el('label','guide-input-label','Количество ламп'),input=el('input','guide-number');input.type='number';input.min='1';input.max='1000000';input.step='1';input.value='1';input.setAttribute('aria-label','Количество ламп');label.append(input);n.append(label,action('Найти подходящие варианты',()=>{if(session!==version)return;if(!input.reportValidity()||!input.value)return;journey.quantity=Number(input.value);finishStep(n);addUser(`Нужно ламп: ${input.value}`);searchLamps({...journey})},'primary-button'));scrollBottom()}
 async function searchLamps(answers){await withBusy(async()=>{const query=['лампа',answers.base,answers.temperature||''].filter(Boolean).join(' ');const data=await api('/api/products?q='+encodeURIComponent(query),undefined,'GET');const matching=(data.products||[]).filter(p=>matchesLamp(p,answers));const available=matching.filter(p=>p.available>=answers.quantity&&p.price!==null);const selected=available.length?available:matching;
  if(!selected.length){const n=help('В полученной выборке нет товара с подтверждёнными параметрами. Это не означает, что его нет во всём магазине. Попробуйте точный артикул или проверьте маркировку; мы не будем подменять выбранный цоколь.');n.append(action('Уточнить маркировку',identify),action('Начать подбор заново',start));return;}
  addResponse({message:available.length?`Ниже варианты с цоколем ${answers.base}, мощностью не выше ${answers.maxWatts} Вт${answers.temperature?', цветом '+answers.temperature+' K':''}. В наличии достаточно для ${answers.quantity} шт. Сверьте также размеры и остальные требования вашего светильника.`:'Нашлись товары с нужными параметрами, но нужное количество или цена не подтверждены. Не заменяем их автоматически.',products:selected,alternatives:[],proposal:null,city:state.city});
  const latest=$('messages').lastElementChild;latest.querySelectorAll('.qty').forEach(input=>{input.value=String(answers.quantity)});const note=el('p','guide-note','Проверены только поля, которые есть в каталоге. Габариты и возможность диммирования требуют отдельной проверки по документации.');latest.append(note);journey=null;version++;
 });}
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
  if(!data.products?.length&&!data.alternatives?.length&&!data.proposal&&!data.options?.length){
   const row=el('div','response-actions');
   row.append(action('Помогите уточнить запрос',start),action('Найти по маркировке',identify));container.append(row);
  }
  if(data.products?.length){
   const row=el('div','response-actions');
   row.append(action('Что значат характеристики?',()=>{
    const n=help('Цоколь — тип соединения лампы. Ватты — мощность, кельвины — цвет света. Минимальная партия — количество, кратно которому продаётся товар. Для кабеля сечение и число жил — разные параметры; их не выбирают только по цене.');
    n.append(action('Вернуться к выбору',()=>container.scrollIntoView({block:'start',behavior:'smooth'})));
   }));container.append(row);
  }
 }
 function reset(){version++;journey=null;currentProposal=null;products.clear();/* Локальный список сохраняется: новый диалог не удаляет выбранные товары. */}
 function lock(value){document.querySelectorAll('[data-start-guide],#prepare-selection,#prepare-selection-dialog').forEach(b=>b.disabled=value||(!b.hasAttribute('data-start-guide')&&!selection.size))}
 function intercept(text){if(state?.ai_enabled)return false;if(/^(?:я\s+)?(?:не знаю|не понимаю).*(?:выбрать|нужно|взять|купить)|^помоги(?:те)?\s+(?:мне\s+)?выбрать\s*[.!?]*$/i.test(text)){addUser(text);$('message-input').value='';start();return true}return false}
 document.querySelectorAll('[data-start-guide]').forEach(button=>button.addEventListener('click',start));
 $('show-selection').addEventListener('click',()=>{renderSelection();$('selection-dialog').showModal()});$('close-selection').addEventListener('click',()=>$('selection-dialog').close());$('prepare-selection').addEventListener('click',prepareSelection);$('prepare-selection-dialog').addEventListener('click',prepareSelection);
 window.FrontendGuide={remember,afterResponse,stage,reset,lock,intercept,ready(serverState){$('build-version').textContent='Версия '+(serverState.version||'local');$('mobile-build-version').textContent='Версия '+(serverState.version||'local')},confirmed(id){if(id===currentProposal)currentProposal=null},cityChanged(){version++;journey=null;products.clear();selection.clear();renderSelection();toast('Город изменён. Найдите товары заново: наличие зависит от склада.')}};
})();
