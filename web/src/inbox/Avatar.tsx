import { useState } from 'preact/hooks';
import { type Chat, initials, net } from './inbox';

/** A chat's picture, or its initials, with its network's badge. */
export function Avatar({ chat, size = 44 }: { chat: Pick<Chat, 'title' | 'avatar' | 'network'>; size?: number }) {
  const [broken, setBroken] = useState(false);
  const n = net(chat.network);
  return (
    <span class="avatar" style={`width:${size}px;height:${size}px`}>
      {chat.avatar && !broken
        ? <img src={chat.avatar} alt="" loading="lazy" onError={() => setBroken(true)} />
        : <span class="avatar-initials">{initials(chat.title)}</span>}
      {chat.network && <i class="net-badge" style={`background:${n.color}`} title={chat.network}>{n.abbr}</i>}
    </span>
  );
}
