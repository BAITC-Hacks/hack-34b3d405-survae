# Контракт для фронтендера: три метода

Бэкенд: Python/FastAPI. Запуск из корня: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
Интерактивная спецификация: `http://localhost:8000/docs`.
Старые дополнительные методы проекта сохранены для совместимости, но новому клиенту достаточно трёх ниже.

## 1. Инициализация и чтение корзины — GET /api/cart

**Сначала вызовите этот метод.** Он ставит HttpOnly cookie сессии и возвращает `csrf`.
Все запросы: `credentials: 'include'`. Все POST: `X-CSRF-Token: csrf` и JSON.
Сессия определяется cookie, не `sessionId` из тела. Токен CSRF не является API-ключом.

```json
{
  "items": [], "total": 0, "count": 0, "url": "/cart", "kind": "demo",
  "notice": "Демонстрационная корзина. Заказы на ekt.kz не оформляются.",
  "csrf": "server-generated-token", "city": "Астана", "proposal": null,
  "catalog_mode": "demo", "ai_enabled": false
}
```

Деньги в тенге (KZT). `count` — сумма количеств; не число уникальных позиций.
`items[].cart_quantity` — количество в корзине; `items[].total` — сумма строки.
Цена и сумма вычисляются сервером. Остатки проверяются для `city`, по умолчанию Астана.

При разработке используйте **одинаковое имя хоста**:
`http://localhost:5173` + `http://localhost:8000` либо `127.0.0.1` с обеих сторон.
Не смешивайте localhost и 127.0.0.1: SameSite cookie не передастся.
Разрешённые origin задаются `FRONTEND_ORIGINS`, без `*`; перезапустите сервер после изменения.
Для production используйте один origin через reverse proxy и HTTPS. Проект — однопроцессный прототип.

## 2. Диалог и подготовка предложения — POST /api/chat

Обычное сообщение:

```json
{"message":"Хочу подсветку кухни","city":"Астана"}
```

`city` необязателен. Его нельзя сменить с непустой корзиной.

Пример ответа (в JSON вместо условных токенов будут настоящие значения):

```json
{
  "message":"Какую длину нужно осветить? Укажите точное число метров, например 2 м.",
  "stage":"clarification",
  "options":["1 м","2 м","3 м","Не знаю"],
  "products":[], "proposal":null, "city":"Астана",
  "cart":{"items":[],"total":0,"count":0,"url":"/cart","kind":"demo","notice":"Демонстрационная корзина"},
  "kit":{
    "scenario":"kitchen_lighting",
    "answers":{"length_m":null,"outlet":null,"dry":null,"color":null},
    "complete":false,"items":[],"checks":[],"unresolved":["Уточните длину"],"mode":"demo"
  },
  "catalog_mode":"demo", "engine":"local"
}
```

`options` — текст кнопок. Нажатие отправляет тот же текст как новое `message`.
Значения `stage`: `clarification` (уточнение), `answer` (ответ/карточки), `proposal` (ждёт подтверждения), `cart_updated` (корзина изменена).
`engine` может отсутствовать для служебных действий. `local` — правила без AI; `openai` — ответ разбора от AI; `fallback` — AI не ответил, применены локальные правила. **Покажите уведомление для fallback**.

Карточки — массив `products`. Существенные поля:

```ts
interface Product {
  id: string; article: string; name: string;
  price: number | null; available: number | null;
  minimum: number; city: string;
  attributes: Record<string, string>;
  certificates: {title: string; url: string}[];
  warnings: string[]; image: string | null; url: string | null;
  mode: 'demo' | 'live'; source: string;
}
```

`null` означает неизвестно, а не ноль. Сертификаты показывать только из массива.
Дополнительно ответ может содержать `alternatives: Product[]`, у каждого аналога поле `reason`, и `sources`.
`kit.items[]`: `{product: Product, quantity: number, purpose: string}`.
`kit.checks[]`: `{name, status, detail}`. `complete:false` и `unresolved` показывать явно.
Полнота относится только к ограниченному согласованному сценарию, не ко всему электромонтажу.

### Выбор товаров вручную через тот же метод

```json
{"action":"propose","items":[{"id":"DEMO-C16-A","quantity":2}]}
```

Сервер проверяет цену, остаток и кратность. Корзина **пока не изменяется**.
Ответ содержит:

```json
{
  "stage":"proposal",
  "proposal":{
    "id":"opaque-proposal-id", "operation":"add", "city":"Астана",
    "expires_at":1800000000,
    "items":[{"id":"DEMO-C16-A","name":"…","price":1390,"requested_quantity":2}],
    "total":2780
  }
}
```

Здесь показаны только ключевые поля; в реальном ответе есть стандартные `message`, `products`, `options`, `cart`, `kit`, `city`, `catalog_mode`.
`expires_at` — Unix-время в секундах. Предложение действует 5 минут.
В комплекте предложение формируется автоматически после уточнений.

Отмена: `{"action":"cancel"}`. Новый диалог: `{"action":"reset"}` (содержимое корзины сохраняется).
Удаление строки: `{"action":"propose","operation":"remove","items":[{"id":"DEMO-C16-A","quantity":1}]}`.
Очистка: `{"action":"propose","operation":"clear"}`. Оба действия также требуют POST /api/cart/confirm;
при remove удаляется вся строка, поле quantity передаётся для совместимости формата.
Любое новое содержательное сообщение аннулирует старое предложение. Отключайте кнопки старых предложений.
Сообщения «да», «да, добавь» не исполняют действие — используйте отдельное подтверждение ниже.

## 3. Подтверждение — POST /api/cart/confirm

```json
{"proposal_id":"ID из proposal.id","confirmed":true}
```

`confirmed` должен быть JSON boolean `true`, не строка. Для старого интерфейса поддерживается `action_id` вместо `proposal_id`.
Ответ имеет обычную структуру чата, `stage: "cart_updated"`, `proposal:null`, обновлённую `cart`.
Повтор одного подтверждения не добавляет товар второй раз. Предложения чужой сессии отклоняются.
При изменении цены/остатка/минимальной партии/характеристик добавление блокируется целиком — ни одна строка комплекта не добавляется частично.

## Ошибки

- 400 — некорректный город или отсутствует явное подтверждение.
- 403 — неверный origin или CSRF. Для истёкшей сессии заново GET /api/cart, затем пользователь повторяет действие; не повторяйте покупку автоматически.
- 409 — предложение устарело, изменился остаток/цена, недостаточно товара или нарушена кратность. Покажите `detail`, обновите корзину и предложите новый выбор.
- 422 — неверный JSON/поля, пустое сообщение, отрицательное количество.
- 429 — ограничение частоты сообщений.

`detail` может быть строкой либо массивом ошибок валидации FastAPI. Никогда не вставляйте текст ответа через небезопасный innerHTML.

## Готовый клиент

Скопируйте `docs/frontend-client.js` в ваш фронтенд. Пример:

```js
import { createBackend } from './frontend-client.js';
const backend = createBackend('http://localhost:8000');
const cart = await backend.init();
const answer = await backend.chat('Хочу подсветку кухни');
// Рендер message, options, products, kit, proposal.
// Только обработчик явного клика подтверждения:
const result = await backend.confirm(answer.proposal.id);
```

Последнюю строку выполняйте только когда `proposal` не null и пользователь проверил состав.
Ссылка `cart.url` относится к origin бэкенда. При отдельном фронтенде либо сделайте свою `/cart`, читающую GET /api/cart, либо откройте `new URL(cart.url, backendBase)`.

## Воспроизводимый сценарий без ключей

GET /api/cart → сообщения по очереди:
1. «Хочу подсветку кухни»;
2. «2 м»;
3. «Есть розетка рядом»;
4. «Сухое место, вдали от воды»;
5. «Тёплый свет».

Получится **учебный**, явно обозначенный комплект: 2 модуля DEMO-BAR-W и 1 блок DEMO-PSU-60, 10 900 ₸.
Корзина пуста до POST /api/cart/confirm. Затем в ней 2 строки, count=3, total=10900.
В live-режиме этот комплект не подставляется; при нехватке данных совместимости возвращается unresolved.
