import asyncio
import os
import re
import secrets
import subprocess
import time
from contextlib import asynccontextmanager, suppress
from typing import Literal

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictBool, AliasChoices, field_validator

from app.catalog import ROOT, Catalog, CITY_NAMES, public_product, stock_for
from app.commerce import new_session, cart_view, prepare, confirm, CommerceError
from app.ai import understand, verify_key, compose_reply
from app.attachments import extract, MAX_BYTES
from app.kits import handle_kit

load_dotenv(ROOT / ".env")
catalog = Catalog()
sessions = {}


def build_version():
    configured = os.getenv("APP_VERSION", "").strip()
    if configured:
        return configured[:40]
    try:
        result = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=2, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "local"


BUILD_VERSION = build_version()


@asynccontextmanager
async def lifespan(app):
    task=asyncio.create_task(catalog.bootstrap())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await catalog.client.aclose()


app=FastAPI(title="SURVAE · EKT Assistant",lifespan=lifespan)
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")


@app.middleware("http")
async def security(request, call_next):
    try:
        size = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        return JSONResponse({"detail":"Некорректный Content-Length."}, status_code=400)
    if size < 0:
        return JSONResponse({"detail":"Некорректный Content-Length."}, status_code=400)
    if size>MAX_BYTES+1024*1024:
        return JSONResponse({"detail":"Размер запроса превышает лимит."},status_code=413)
    session_id=request.cookies.get("survae_session")
    new = session_id not in sessions
    if new:
        if len(sessions)>=500:
            oldest=min(sessions,key=lambda key:sessions[key]["touched"])
            sessions.pop(oldest,None)
        session_id=secrets.token_urlsafe(32)
        sessions[session_id]=new_session()
    session=sessions[session_id]
    session["touched"]=time.time()
    request.state.session=session
    if request.method in {"POST","PUT","PATCH","DELETE"}:
        origin=request.headers.get("origin")
        expected=str(request.base_url).rstrip("/")
        if origin and origin not in {expected, *ALLOWED_ORIGINS}:
            return JSONResponse({"detail":"Недопустимый источник запроса."},status_code=403)
        if not secrets.compare_digest(request.headers.get("x-csrf-token",""),session["csrf"]):
            return JSONResponse({"detail":"Обновите страницу и повторите действие."},status_code=403)
    response=await call_next(request)
    if new:
        response.set_cookie("survae_session",session_id,httponly=True,samesite="strict",secure=request.url.scheme=="https",max_age=43200)
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["X-Frame-Options"]="SAMEORIGIN"
    response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' https://ekt.kz data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith("/api") or request.url.path in {"/", "/cart"}:
        response.headers["Cache-Control"]="no-store"
    return response


# Outer middleware handles preflight and adds CORS headers to error responses.
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS,
                   allow_credentials=True, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type", "X-CSRF-Token"])


@app.exception_handler(CommerceError)
async def commerce_error(request,exc):
    return JSONResponse({"detail":str(exc),"cart":cart_view(request.state.session)},status_code=409)


def response(session, text, products=None, **extra):
    return {"message":text,"products":[public_product(p,session["city"]) for p in products or []],
            "cart":cart_view(session),"proposal":session["pending"],"city":session["city"],
            "stage":"proposal" if session["pending"] else "answer", "options":[],
            "kit":None, "catalog_mode":catalog.mode, **extra}


@app.get("/")
@app.get("/cart")
async def index():
    return FileResponse(ROOT/"static/index.html")


@app.get("/api/state")
async def state(request:Request):
    s=request.state.session
    return {"csrf":s["csrf"],"city":s["city"],"cart":cart_view(s),"proposal":s["pending"],
            "catalog":{"mode":catalog.mode,"count":len(catalog.items),"loading":catalog.loading,"error":catalog.error,
                       "notice":"Репрезентативная выборка каталога"},
            "ai_enabled":bool(os.getenv("OPENAI_API_KEY")),"setup_allowed":local_setup_allowed(request),
            "version":BUILD_VERSION}


class CityInput(BaseModel):
    city:str=Field(max_length=40)


@app.post("/api/city")
async def change_city(body:CityInput, request:Request):
    s=request.state.session
    if body.city.lower() not in CITY_NAMES:
        raise HTTPException(400,"Выберите город из списка.")
    async with s["lock"]:
        if s["cart"] and body.city!=s["city"]:
            raise HTTPException(409,"Сначала подтвердите очистку корзины: товары в ней выбраны для текущего города.")
        s["city"]=body.city
        s["pending"]=None
        return response(s,f"Выбран город {body.city}. Наличие проверяется по его складам.")


@app.get("/api/products")
async def products(request:Request,q:str=""):
    if q:
        found=await catalog.search(q[:200],6)
    else:
        ids=[p["id"] for p in list(catalog.items.values())[:6]]
        found=[p for p in await asyncio.gather(*(catalog.detail(i) for i in ids)) if p]
    return {"products":[public_product(p,request.state.session["city"]) for p in found]}


@app.get("/api/cart")
async def get_cart(request:Request):
    s = request.state.session
    return {**cart_view(s), "csrf":s["csrf"], "city":s["city"],
            "proposal":s["pending"], "catalog_mode":catalog.mode,
            "ai_enabled":bool(os.getenv("OPENAI_API_KEY"))}


class Line(BaseModel):
    id:str=Field(max_length=40)
    quantity:float=Field(gt=0,le=1000000,allow_inf_nan=False)


class ProposalInput(BaseModel):
    items:list[Line]=Field(default_factory=list,max_length=12)
    operation:str="add"


@app.post("/api/cart/propose")
async def propose_cart(body:ProposalInput,request:Request):
    s=request.state.session
    async with s["lock"]:
        await prepare(catalog,s,[line.model_dump() for line in body.items],body.operation)
        return response(s,"Проверьте товары, количество и сумму. Корзина изменится только после подтверждения.")


class ConfirmationInput(BaseModel):
    action_id:str=Field(min_length=1,max_length=100,
                        validation_alias=AliasChoices("proposal_id", "action_id"))
    confirmed:StrictBool=False


@app.post("/api/cart/confirm")
async def confirm_cart(body:ConfirmationInput,request:Request):
    s=request.state.session
    async with s["lock"]:
        if not body.confirmed:
            raise HTTPException(400,"Необходимо явное подтверждение.")
        return response(s,await confirm(catalog,s,body.action_id),stage="cart_updated")


@app.post("/api/cart/cancel")
async def cancel_cart(request:Request):
    async with request.state.session["lock"]:
        request.state.session["pending"]=None
        return response(request.state.session,"Отменено. Корзина не изменилась.")


@app.post("/api/upload")
async def upload(request:Request,file:UploadFile=File(...)):
    s=request.state.session
    try:
        body=await file.read(MAX_BYTES+1)
        data=await asyncio.to_thread(extract,(file.filename or "file")[:160],body)
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    except Exception:
        raise HTTPException(400,"Не удалось прочитать файл. Проверьте формат и отсутствие пароля.")
    finally:
        await file.close()
    if data.get("image") and not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(400,"Для распознавания фото подключите AI. Текстовые документы уже доступны.")
    attachment_id=secrets.token_urlsafe(16)
    s["attachments"]={attachment_id:data}
    return {"attachment_id":attachment_id,"name":data["name"],"text_preview":data.get("text","")[:180]}


def purchase_conditions(topic, products):
    if topic=="minimum":
        if products:
            return "Минимальная партия по данным каталога:\n"+"\n".join(f"• {p['article']}: {p['minimum']:g} ед." for p in products)
        return "Минимальная партия зависит от товара. Укажите артикул — проверю поле минимальной партии в каталоге."
    if topic=="payment":
        return "По опубликованным условиям ekt.kz: физлица могут оплатить картой онлайн, наличными при получении либо наличными/через POS-терминал при самовывозе в торговом зале. Юрлица — перечислением по счёту либо наличными в торговом зале при самовывозе. Платёжные данные в чате не запрашиваются. Актуальные способы для заказа подтвердит магазин."
    if topic=="delivery":
        return "По условиям ekt.kz, согласованный с менеджером товар по Алматы доставляется в течение 48 часов. Стоимость и сроки доставки в другие города согласовываются с менеджером и зависят от адреса, веса и объёма. Доступен самовывоз. Наличие товара в городе не является обещанием срока доставки."
    return "Помогу проверить оплату, доставку и минимальную партию. Напишите интересующий вопрос и, если он о конкретной позиции, артикул."


class ChatInput(BaseModel):
    message:str=Field(default="",max_length=4000)
    attachment_id:str|None=Field(default=None,max_length=100)
    action:Literal["message", "propose", "cancel", "reset"]="message"
    operation:Literal["add", "remove", "clear"]="add"
    items:list[Line]=Field(default_factory=list,max_length=12)
    city:str|None=Field(default=None,max_length=40)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value):
        return value.strip()


@app.post("/api/chat/reset")
async def reset_chat(request:Request):
    s=request.state.session
    async with s["lock"]:
        s["history"]=[]
        s["last_products"]=[]
        s["attachments"]={}
        s["pending"]=None
        s["kit"]=None
        s["ai_clarification"]=False
        return response(s,"Начат новый диалог.")


@app.post("/api/chat")
async def chat(body:ChatInput,request:Request):
    s=request.state.session
    now=time.time()
    calls=[t for t in s.get("calls",[]) if now-t<60]
    if len(calls)>=25:
        raise HTTPException(429,"Слишком много сообщений. Попробуйте через минуту.")
    s["calls"]=calls+[now]
    async with s["lock"]:
        if body.city is not None:
            city = body.city.strip().lower()
            if city not in CITY_NAMES:
                raise HTTPException(400,"Выберите поддерживаемый город.")
            if city != s["city"].lower():
                if s["cart"]:
                    raise HTTPException(409,"Город нельзя изменить при непустой корзине.")
                s["city"] = city.capitalize()
                s["pending"] = None
        if body.action == "reset":
            s.update(pending=None, history=[], last_products=[], kit=None, ai_clarification=False)
            return response(s,"Новый диалог. Опишите задачу, например: хочу подсветку кухни.",
                            stage="clarification", options=["Хочу подсветку кухни"])
        if body.action == "cancel":
            s["pending"] = None
            return response(s,"Отменено. Корзина не изменилась.")
        if body.action == "propose":
            s["pending"] = None
            await prepare(catalog,s,[line.model_dump() for line in body.items],body.operation)
            return response(s,"Проверьте состав и количество. Подтвердите добавление кнопкой.")
        if not body.message:
            raise HTTPException(422,"Сообщение не должно быть пустым.")
        message=body.message.strip()
        normal=re.sub(r"[\s,!?.]+"," ",message.lower()).strip()
        if (normal in {"отмена","отмени","не добавляй","не надо","жоқ"}
                or (normal == "нет" and (s["pending"] or (not s.get("kit") and not s.get("ai_clarification"))))
                or "не добавляй" in normal):
            s["pending"]=None
            return response(s,"Отменено. Корзина не изменилась.")
        if normal in {"да","да добавь","добавь","подтверждаю","да подтверждаю","да удалить","да очистить","иә қос"} and s["pending"]:
            return response(s,"Подтвердите конкретное предложение кнопкой. Сообщение само по себе корзину не меняет.")
        # A new substantive message invalidates an earlier confirmation context.
        s["pending"]=None
        kit_result = await handle_kit(catalog, s, message)
        if kit_result is not None:
            result = response(s, **kit_result)
            s["history"]=(s["history"]+[{"role":"user","text":message},
                {"role":"assistant","text":result["message"]}])[-12:]
            return result
        attachment=s["attachments"].pop(body.attachment_id,None) if body.attachment_id else None
        plan,engine=await understand(message,s["history"],s["last_products"],attachment)
        if attachment and engine!="openai" and attachment.get("text"):
            plan["query"]=attachment["text"][:1000]
        intent=plan["intent"]
        s["ai_clarification"] = intent == "clarify"
        query=plan.get("query") or message
        chosen=[]
        if plan.get("product_id"):
            p=await catalog.detail(plan["product_id"])
            chosen=[p] if p else []
        if not chosen and (intent not in {"greeting","conditions","clarify"} or plan.get("topic") == "minimum"):
            chosen=await catalog.search(query)
        if intent=="clarify":
            result=response(s,plan.get("question") or "Что вы хотите сделать и что у вас уже есть?",engine=engine,stage="clarification")
        elif intent=="greeting":
            result=response(s,"Здравствуйте! Подберу товар по артикулу или характеристикам, проверю наличие и помогу собрать корзину. Что ищете?",engine=engine)
        elif intent=="conditions":
            source=[{"title":"Оплата и доставка ekt.kz","url":"https://ekt.kz/checkout-delivery/"}]
            result=response(s,purchase_conditions(plan["topic"],chosen or s["last_products"]),sources=source,engine=engine)
        elif not chosen:
            text="В подключённой выборке не нашёл подходящую позицию. Уточните артикул, производителя или характеристики."
            if catalog.loading:text="Каталог ещё загружается. Повторите запрос через несколько секунд."
            if catalog.error:text=catalog.error
            result=response(s,text,engine=engine)
        else:
            s["last_products"]=chosen
            if intent=="propose":
                exact=[p for p in chosen if p["article"].lower() in message.lower() or p["id"]==plan.get("product_id")]
                if len(chosen)>1 and len(exact)!=1:
                    result=response(s,"Нашёл несколько вариантов. Выберите карточку товара, чтобы подготовить добавление.",chosen,engine=engine)
                elif plan.get("quantity") is None:
                    result=response(s,"Какое количество добавить? Можно указать его на карточке товара.",exact or chosen,engine=engine)
                else:
                    target=(exact or chosen)[0]
                    await prepare(catalog,s,[{"id":target["id"],"quantity":plan["quantity"]}])
                    result=response(s,"Проверьте предложение ниже. Добавить в корзину?",engine=engine)
            elif intent=="alternatives" or (len(chosen)==1 and stock_for(chosen[0],s["city"])==0):
                original=chosen[0]
                alternatives=await catalog.alternatives(original,s["city"])
                text=f"Проверил альтернативы для {original['article']} в городе {s['city']}. "
                text+=("Ниже варианты с совпадающими проверенными характеристиками." if alternatives else "В доступной выборке нет подтверждённого аналога. Передайте запрос менеджеру: недостаточно совпадающих данных для надёжной замены.")
                result=response(s,text,[original],alternatives=[public_product(p,s["city"]) for p in alternatives],engine=engine)
                s["last_products"]=alternatives or chosen
            elif plan["topic"]=="certificate":
                has=any(p["certificates"] for p in chosen)
                result=response(s,"Ссылки на документы указаны в карточках." if has else "В полученных данных сертификаты не указаны. Подтвердить их наличие не могу; запросите документы у менеджера.",chosen,engine=engine)
            else:
                result=response(s, "Вот сравнение по данным каталога." if intent=="compare" else f"Нашёл подходящие позиции. Наличие показано для города {s['city']}.",chosen,engine=engine)
        # Keep deterministic commerce/certificate answers and confirmation unchanged.
        if engine == "openai" and intent not in {"conditions", "clarify", "propose"} and plan.get("topic") != "certificate":
            try:
                explanation = await compose_reply(message, s["history"], {
                    "result": result, "catalog_mode": catalog.mode})
                result["message"] = explanation + "\n\n" + result["message"]
            except Exception:
                result["engine"] = "fallback"
        s["history"]=(s["history"]+[{"role":"user","text":message},{"role":"assistant","text":result["message"]}])[-12:]
        return result


def local_setup_allowed(request):
    return os.getenv("ALLOW_LOCAL_SETUP")=="1" and request.client.host in {"127.0.0.1","::1","testclient"}


@app.get("/setup")
async def setup_page(request:Request):
    if not local_setup_allowed(request):raise HTTPException(404)
    return FileResponse(ROOT/"static/setup.html")


class SetupInput(BaseModel):
    api_key:str=Field(min_length=20,max_length=300)


@app.post("/api/setup")
async def save_key(body:SetupInput,request:Request):
    if not local_setup_allowed(request):raise HTTPException(404)
    if "\n" in body.api_key or "\r" in body.api_key or not body.api_key.startswith("sk-"):
        raise HTTPException(400,"Проверьте API-ключ.")
    try:
        await verify_key(body.api_key)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from None
    env_path=ROOT/".env"
    env_path.touch(mode=0o600,exist_ok=True)
    os.chmod(env_path,0o600)
    set_key(str(env_path),"OPENAI_API_KEY",body.api_key)
    os.environ["OPENAI_API_KEY"]=body.api_key
    return {"ok":True}
