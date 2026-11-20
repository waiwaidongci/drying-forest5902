export const routes=['overview','source','config','review','results','notes','audit'];
export function route(){const hash=location.hash.slice(1)||'overview';return routes.includes(hash)?hash:'overview'}
