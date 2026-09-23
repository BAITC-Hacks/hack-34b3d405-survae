/** Browser ES module. No API keys in the frontend. */
export function createBackend(base = '') {
  let csrf = '';
  const request = async (path, body) => {
    const response = await fetch(base + path, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'include',
      headers: body === undefined ? {} : {
        'Content-Type': 'application/json', 'X-CSRF-Token': csrf,
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте введённые данные');
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  };
  return {
    async init() {
      const cart = await request('/api/cart');
      csrf = cart.csrf;
      return cart;
    },
    chat: (message, city) => request('/api/chat', { message, ...(city ? { city } : {}) }),
    propose: items => request('/api/chat', { action: 'propose', items }),
    proposeRemoval: id => request('/api/chat', { action: 'propose', operation: 'remove', items: [{ id, quantity: 1 }] }),
    proposeClear: () => request('/api/chat', { action: 'propose', operation: 'clear' }),
    cancel: () => request('/api/chat', { action: 'cancel' }),
    reset: () => request('/api/chat', { action: 'reset' }),
    confirm: proposal_id => request('/api/cart/confirm', { proposal_id, confirmed: true }),
    cart: () => request('/api/cart'),
  };
}
