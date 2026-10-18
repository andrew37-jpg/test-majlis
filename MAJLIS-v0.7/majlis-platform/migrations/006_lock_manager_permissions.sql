-- Runtime must not gain UPDATE on access tables just to lock their rows.
-- Fixed SQL, pinned search_path, no dynamic queries or caller supplied identifiers.
CREATE FUNCTION public.manager_order_access(manager_id BIGINT, target_order BIGINT)
RETURNS BOOLEAN LANGUAGE SQL VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $$
 SELECT a.can_edit FROM public.order_managers a
 JOIN public.manager_access m ON m.telegram_id=a.manager_id
 WHERE a.order_id=target_order AND a.manager_id=manager_order_access.manager_id
 AND m.enabled FOR SHARE OF a,m
$$;
REVOKE ALL ON FUNCTION public.manager_order_access(BIGINT,BIGINT) FROM PUBLIC;
CREATE FUNCTION public.manager_create_permission(manager_id BIGINT)
RETURNS BOOLEAN LANGUAGE SQL VOLATILE SECURITY DEFINER SET search_path=pg_catalog
AS $$
 SELECT m.can_create FROM public.manager_access m
 WHERE m.telegram_id=manager_id AND m.enabled FOR SHARE OF m
$$;
REVOKE ALL ON FUNCTION public.manager_create_permission(BIGINT) FROM PUBLIC;
