/**
 * Shows the text of ⓘ info icons in a popup on tap, click, or hover. Their title attribute alone
 * only works as a hover tooltip, which touch screens can't show.
 */
document.addEventListener('DOMContentLoaded', () => {
    const popup = document.createElement('div');
    popup.className = 'info-tip-popup';
    popup.setAttribute('role', 'tooltip');
    document.body.appendChild(popup);
    let pinned = null;

    function show(icon) {
        popup.textContent = icon.dataset.tip;
        popup.style.display = 'block';
        // Below the icon if it fits, otherwise above, and always inside the screen's edges.
        const rect = icon.getBoundingClientRect();
        const left = rect.left + rect.width / 2 - popup.offsetWidth / 2;
        const below = rect.bottom + 6;
        popup.style.left = `${Math.min(Math.max(left, 8), window.innerWidth - popup.offsetWidth - 8)}px`;
        popup.style.top = `${below + popup.offsetHeight > window.innerHeight ? rect.top - popup.offsetHeight - 6 : below}px`;
    }

    function hide() {
        popup.style.display = 'none';
        pinned = null;
    }

    document.querySelectorAll('[title]').forEach(icon => {
        if (icon.textContent.trim() !== 'ⓘ') return;
        icon.dataset.tip = icon.title;
        icon.removeAttribute('title');
        icon.classList.add('info-tip');
        icon.setAttribute('tabindex', '0');
        icon.setAttribute('role', 'button');
        icon.setAttribute('aria-label', icon.dataset.tip);

        icon.addEventListener('click', event => {
            // Keeps the click from also reaching handlers around the icon, like selecting a radio button.
            event.stopPropagation();
            event.preventDefault();
            if (pinned === icon) {
                hide();
            } else {
                pinned = icon;
                show(icon);
            }
        });
        icon.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                icon.click();
            }
        });
        icon.addEventListener('mouseenter', () => { if (!pinned) show(icon); });
        icon.addEventListener('mouseleave', () => { if (!pinned) popup.style.display = 'none'; });
    });

    document.addEventListener('click', hide);
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
    window.addEventListener('scroll', hide, true);
});
