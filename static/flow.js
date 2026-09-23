'use strict';
/* Presentation only. The server owns catalogue facts and cart mutations. */
(function(root){
 const labels=['Задача','Уточнения','Товары','Проверка','Подтверждение','Корзина'];
 function step(data){
  if(data.stage==='cart_updated')return 5;
  if(data.proposal)return 3;
  if(data.stage==='clarification'||data.options?.length||data.kit?.complete===false)return 1;
  if(data.products?.length||data.kit?.items?.length)return 2;
  return 0;
 }
 function canConfirm(proposal,activeId,now=Date.now()){
  return !!proposal&&proposal.id===activeId&&Number.isFinite(proposal.expires_at)&&proposal.expires_at*1000>now;
 }
 function needsContextChoice(context,message){
  return !!context?.kit && !context.options?.includes(message) && !/^(да|нет|отмена|подтверждаю)[.!?]*$/i.test(message.trim());
 }
 const api={labels,step,canConfirm,needsContextChoice};
 if(typeof module!=='undefined')module.exports=api;
 else root.ShoppingFlow=api;
})(typeof window==='undefined'?{}:window);
