// Card readers (Stripe / Square Terminal) are switched off on the server until a shop can connect
// one (backend settings.CARD_READERS_ENABLED, sent as /auth/me card_readers). Every reader entry
// point asks this: off hides it, and nothing calls /payments/terminal.
import { useMe } from "../context/Me";

export function useCardReaders(): boolean {
  return useMe().me?.card_readers === true;
}
