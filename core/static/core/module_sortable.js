/* Pointer-based sorting keeps editable controls and existing DOM nodes intact. */
window.ModuleSortable = (() => {
  let active = null;
  let saving = false;
  const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

  function attach(list, save, canStart = () => true) {
    list.classList.add('sortable-list');
    let gesture = null;
    let busy = false;
    let suppressClickUntil = 0;
    const items = () => Array.from(list.children).filter(node => node.matches('[data-sort-key]'));
    const animateMove = (change) => {
      const previous = new Map(items().map(node => [node, node.getBoundingClientRect().top]));
      change();
      if (reducedMotion()) return;
      items().forEach(node => {
        const delta = previous.get(node) - node.getBoundingClientRect().top;
        if (delta && Number.isFinite(delta)) {
          node.getAnimations().forEach(animation => animation.cancel());
          node.animate([{transform:`translateY(${delta}px)`}, {transform:'translateY(0)'}], {duration:180, easing:'ease-out'});
        }
      });
    };
    const restore = order => animateMove(() => order.forEach(node => list.append(node)));
    async function persist(order, moved) {
      if (items().every((node, index) => node === order[index])) return;
      busy = true;
      saving = true;
      list.setAttribute('aria-busy', 'true');
      try {
        if (await save(items().map(node => node.dataset.sortKey)) === false) restore(order);
        else if (moved.isConnected && !reducedMotion()) {
          moved.animate([{boxShadow:'0 0 0 3px #38bdf8'}, {boxShadow:'0 0 0 0 transparent'}], {duration:450});
        }
      } catch (error) {
        restore(order);
      } finally {
        busy = false;
        saving = false;
        list.removeAttribute('aria-busy');
      }
    }
    function place() {
      const rect = list.getBoundingClientRect();
      if (gesture.x < rect.left - 36 || gesture.x > rect.right + 36) return;
      const before = items().find(node => node !== gesture.node && gesture.y < rect.top + node.offsetTop - list.scrollTop + node.offsetHeight / 2);
      if (before === gesture.node.nextElementSibling || (!before && list.lastElementChild === gesture.node)) return;
      animateMove(() => list.insertBefore(gesture.node, before || null));
    }
    function scrollFrame() {
      if (!gesture?.dragging) return;
      let scroller = list.parentElement;
      while (scroller && !(scroller.scrollHeight > scroller.clientHeight && /auto|scroll/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
      const rect = scroller ? scroller.getBoundingClientRect() : {top:0, bottom:innerHeight};
      const top = Math.max(0, rect.top), bottom = Math.min(innerHeight, rect.bottom);
      const delta = gesture.y < top + 60 ? -Math.min(16, (top + 60 - gesture.y) / 4) : gesture.y > bottom - 60 ? Math.min(16, (gesture.y - bottom + 60) / 4) : 0;
      if (delta) {
        if (scroller) scroller.scrollTop += delta;
        else window.scrollBy(0, delta);
        place();
      }
      gesture.frame = requestAnimationFrame(scrollFrame);
    }
    function begin() {
      gesture.dragging = true;
      gesture.order = items();
      active = list;
      const rect = gesture.node.getBoundingClientRect();
      const ghost = gesture.node.cloneNode(true);
      ghost.removeAttribute('id');
      ghost.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
      ghost.setAttribute('aria-hidden', 'true');
      ghost.classList.add('sort-ghost');
      ghost.style.cssText = `position:fixed;left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;margin:0;box-sizing:border-box;pointer-events:none;z-index:5000;`;
      document.body.append(ghost);
      gesture.ghost = ghost;
      gesture.node.classList.add('sort-source');
      document.documentElement.classList.add('is-sorting');
      list.setPointerCapture(gesture.pointerId);
      gesture.frame = requestAnimationFrame(scrollFrame);
    }
    function finish(cancel = false) {
      if (!gesture) return;
      const finished = gesture;
      gesture = null;
      if (!finished.dragging) return;
      cancelAnimationFrame(finished.frame);
      finished.ghost.remove();
      finished.node.classList.remove('sort-source');
      document.documentElement.classList.remove('is-sorting');
      if (list.hasPointerCapture(finished.pointerId)) list.releasePointerCapture(finished.pointerId);
      active = null;
      suppressClickUntil = Date.now() + 350;
      if (cancel) restore(finished.order);
      else persist(finished.order, finished.node);
    }
    list.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !event.isPrimary || busy || active || saving || !canStart()) return;
      const node = event.target.closest('[data-sort-key]');
      if (!node || node.parentElement !== list) return;
      const handle = event.target.closest('.sort-handle');
      if (!handle && event.target.closest('button,input,textarea,select,a,iframe,[contenteditable]')) return;
      // Touch scrolling remains available outside the dedicated handle.
      if (event.pointerType === 'touch' && !handle) return;
      gesture = {node, pointerId:event.pointerId, startX:event.clientX, startY:event.clientY, x:event.clientX, y:event.clientY};
    });
    window.addEventListener('pointermove', event => {
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      gesture.x = event.clientX; gesture.y = event.clientY;
      if (!gesture.dragging && Math.hypot(gesture.x - gesture.startX, gesture.y - gesture.startY) >= 6) begin();
      if (!gesture.dragging) return;
      event.preventDefault();
      gesture.ghost.style.transform = `translate(${gesture.x - gesture.startX}px,${gesture.y - gesture.startY}px) rotate(1deg)`;
      place();
    }, {passive:false});
    window.addEventListener('pointerup', event => {
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      const rect = list.getBoundingClientRect();
      finish(event.clientX < rect.left - 36 || event.clientX > rect.right + 36 || event.clientY < rect.top - 60 || event.clientY > rect.bottom + 60);
    });
    window.addEventListener('pointercancel', () => finish(true));
    window.addEventListener('blur', () => finish(true));
    list.addEventListener('lostpointercapture', () => finish(true));
    document.addEventListener('keydown', event => {
      if (gesture && event.key === 'Escape') { event.preventDefault(); finish(true); }
    });
    list.addEventListener('click', event => {
      if (Date.now() < suppressClickUntil || event.target.closest('.sort-handle')) {
        event.preventDefault(); event.stopImmediatePropagation();
      }
    }, true);
    list.addEventListener('dragstart', event => event.preventDefault());
    list.addEventListener('keydown', event => {
      if (!event.target.matches('.sort-handle') || !['ArrowUp', 'ArrowDown'].includes(event.key) || busy || active || saving || !canStart()) return;
      event.preventDefault();
      const node = event.target.closest('[data-sort-key]'), order = items(), index = order.indexOf(node);
      const target = order[index + (event.key === 'ArrowUp' ? -1 : 1)];
      if (!target) return;
      animateMove(() => list.insertBefore(node, event.key === 'ArrowUp' ? target : target.nextSibling));
      event.target.focus();
      persist(order, node);
    });
    return {cancel:() => finish(true), get busy(){return busy || !!gesture?.dragging;}};
  }
  return {attach};
})();
