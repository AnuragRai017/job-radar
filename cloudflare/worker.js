const DEFAULT_API_ORIGIN = "https://extras-gray-que-states.trycloudflare.com";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname.startsWith("/api/") || url.pathname === "/health") {
      const origin = env.API_ORIGIN || DEFAULT_API_ORIGIN;
      const target = new URL(url.pathname + url.search, origin);
      const init = {
        method: request.method,
        headers: request.headers,
        redirect: "manual",
      };
      if (request.method !== "GET" && request.method !== "HEAD") {
        init.body = request.body;
      }
      return fetch(target, init);
    }

    return env.ASSETS.fetch(request);
  },
};
