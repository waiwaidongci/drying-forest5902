"""同温同风组内冲突复核的核心算法（纯标准库实现，worker 与测试共用）。

冲突定义：同组（同设定温度、同风量）内，试件 b 的湿度与厚度都不低于 a，
而实测时长却严格更短，则 a、b 构成一对冲突。

数学模型：令 H=湿度, D=厚度, L=实测时长，定义严格偏序
    a ≺ b  ⟺  H_a ≤ H_b 且 D_a ≤ D_b 且 L_a > L_b
一对试件冲突 ⟺ 二者在该偏序下可比。于是：

* 移出后剩余集合无冲突 ⟺ 剩余集合是该偏序的反链（antichain）；
* 最少待复核数 = n − 最大反链大小；
* Dilworth 定理：最大反链大小 = 最小链覆盖的链数 c，
  故最少待复核数 = n − c。

最优性证书（互不重叠的冲突链）：最小链覆盖给出 c 条两两不相交、合起来
覆盖全部 n 件的冲突链。链上任意两件都可比（互相冲突），任何无冲突保留
方案在每条长度为 k 的链中至多保留 1 件、至少移出 k−1 件；合计至少移出
n−c 件。本算法恰好移出 n−c 件，下界与方案相等，证明再少就不行。

计算路径（不做 O(n²) 逐对枚举）：
1. 最小链覆盖经二部匹配求解：左右各放全部试件，边 u→v ⟺ u ≺ v，
   最大匹配为 m，则 c = n − m（标准链覆盖构造）。
2. u 的邻点是一个三维正交范围 {v : H_v ≥ H_u, D_v ≥ D_u, L_v < L_u}，
   用 KD 树按需做范围报告；Hopcroft–Karp 只在分层图触及的顶点上生成
   邻接，从不逐对扫描，同工况上万件也无需枚举全部试件对。
3. 最大反链由匹配的交替可达集（Kőnig–Dilworth 标准构造）还原：
   从未匹配左点出发，沿「非匹配边（左→右）、匹配边（右→左）」交替
   BFS 得集合 Z，保留集 = {v : 左v ∈ Z 且 右v ∉ Z}。

校验：verify_group 用排序 + 树状数组在 O(n log n) 内独立确认保留集
无冲突、冲突链互不相交且覆盖全部、最优性等式成立。
"""

from collections import deque

__all__ = ['analyze_group', 'verify_group', 'brute_force_min_review']


def _rank_map(values):
    """把取值映射为 0..k-1 的秩，保持全部大小关系（含相等）。"""
    return {v: i for i, v in enumerate(sorted(set(values)))}


class _DominanceIndex:
    """静态三维支配范围索引（KD 树）。

    query(hmin, dmin, lmax) 报告所有满足
    H >= hmin 且 D >= dmin 且 L < lmax 的点下标。
    另维护各子树未匹配右点计数，支持贪心种子的 find_free/mark_matched。
    """

    __slots__ = ('h', 'd', 'l', 'perm', 'pos', 'nodes')

    _LEAF = 16
    # 节点字段下标
    _LO, _HI, _LEFT, _RIGHT = 0, 1, 2, 3
    _MINH, _MAXH, _MIND, _MAXD, _MINL, _MAXL, _FREE = 4, 5, 6, 7, 8, 9, 10

    def __init__(self, h, d, l):
        self.h, self.d, self.l = h, d, l
        self.perm = list(range(len(h)))
        # 节点: [lo, hi, left, right, min_h, max_h, min_d, max_d, min_l, max_l, free]
        # left == -1 表示叶子；子树对应 perm[lo:hi]；free 为子树内未匹配右点数
        self.nodes = []
        if self.perm:
            self._build(0, len(self.perm))
        self.pos = [0] * len(h)
        for p, i in enumerate(self.perm):
            self.pos[i] = p

    def _build(self, lo, hi):
        idx = len(self.nodes)
        self.nodes.append(None)
        h, d, l, perm = self.h, self.d, self.l, self.perm
        seg = perm[lo:hi]
        min_h = min(h[i] for i in seg)
        max_h = max(h[i] for i in seg)
        min_d = min(d[i] for i in seg)
        max_d = max(d[i] for i in seg)
        min_l = min(l[i] for i in seg)
        max_l = max(l[i] for i in seg)
        size = hi - lo
        if size <= self._LEAF:
            self.nodes[idx] = [lo, hi, -1, -1, min_h, max_h,
                               min_d, max_d, min_l, max_l, size]
            return idx
        spread_h = max_h - min_h
        spread_d = max_d - min_d
        spread_l = max_l - min_l
        if spread_h >= spread_d and spread_h >= spread_l:
            key = h
        elif spread_d >= spread_l:
            key = d
        else:
            key = l
        seg.sort(key=key.__getitem__)
        perm[lo:hi] = seg
        mid = (lo + hi) // 2
        left = self._build(lo, mid)
        right = self._build(mid, hi)
        self.nodes[idx] = [lo, hi, left, right, min_h, max_h,
                           min_d, max_d, min_l, max_l, size]
        return idx

    def query(self, hmin, dmin, lmax):
        out = []
        if not self.nodes:
            return out
        h, d, l, perm, nodes = self.h, self.d, self.l, self.perm, self.nodes
        stack = [0]
        while stack:
            node = nodes[stack.pop()]
            if node[5] < hmin or node[7] < dmin or node[8] >= lmax:
                continue
            lo, hi = node[0], node[1]
            if node[4] >= hmin and node[6] >= dmin and node[9] < lmax:
                out.extend(perm[lo:hi])
                continue
            if node[2] == -1:
                for i in perm[lo:hi]:
                    if h[i] >= hmin and d[i] >= dmin and l[i] < lmax:
                        out.append(i)
            else:
                stack.append(node[2])
                stack.append(node[3])
        return out

    def find_free(self, hmin, dmin, lmax, match_r):
        """在范围内任找一个未匹配右点；无则返回 -1。供贪心种子使用。"""
        if not self.nodes:
            return -1
        h, d, l, perm, nodes = self.h, self.d, self.l, self.perm, self.nodes
        stack = [0]
        while stack:
            node = nodes[stack.pop()]
            if node[10] == 0 or node[5] < hmin or node[7] < dmin or node[8] >= lmax:
                continue
            if node[2] == -1:
                for i in perm[node[0]:node[1]]:
                    if match_r[i] == -1 and h[i] >= hmin and d[i] >= dmin and l[i] < lmax:
                        return i
            else:
                stack.append(node[2])
                stack.append(node[3])
        return -1

    def mark_matched(self, v):
        """右点 v 被匹配后，沿根到叶路径递减未匹配计数（单调不回升）。"""
        p = self.pos[v]
        nodes = self.nodes
        idx = 0
        while True:
            node = nodes[idx]
            node[10] -= 1
            if node[2] == -1:
                return
            nxt = nodes[node[2]]
            idx = node[2] if nxt[0] <= p < nxt[1] else node[3]


def _max_matching(n, index, cache_budget=24_000_000):
    """二部图最大匹配（Hopcroft–Karp，邻接由索引按需生成）。

    左、右各为 0..n-1；边 u→v ⟺ u ≺ v。返回 (match_l, match_r)。
    先用索引的范围查询做 O(n log n) 贪心极大匹配，把增广阶段限制在
    少量空闲点可达的分层图内；邻接跨阶段缓存（带容量上限），
    整个过程从不逐对枚举。
    """
    match_l = [-1] * n
    match_r = [-1] * n

    # 贪心种子：候选面最窄的左点优先（H、D 越大可选右点越少），
    # O(n log n) 的极大匹配可把增广阶段压缩到少量空闲点可达的分层图内
    for u in sorted(range(n), key=lambda u: (-index.h[u] - index.d[u], index.l[u])):
        v = index.find_free(index.h[u], index.d[u], index.l[u], match_r)
        if v != -1:
            match_l[u] = v
            match_r[v] = u
            index.mark_matched(v)

    cache = [None] * n
    cached = 0

    while True:
        # BFS：从未匹配左点出发分层，记录最短增广路到达的左点层数
        dist = [-1] * n
        dq = deque()
        for u in range(n):
            if match_l[u] == -1:
                dist[u] = 0
                dq.append(u)
        if not dq:
            break
        free_level = -1
        while dq:
            u = dq.popleft()
            du = dist[u]
            if free_level != -1 and du >= free_level:
                continue
            a = cache[u]
            if a is None:
                a = index.query(index.h[u], index.d[u], index.l[u])
                if cached + len(a) <= cache_budget:
                    cache[u] = a
                    cached += len(a)
            nxt = du + 1
            for v in a:
                w = match_r[v]
                if w == -1:
                    free_level = du
                elif dist[w] == -1:
                    dist[w] = nxt
                    dq.append(w)
        if free_level == -1:
            break
        # DFS：沿分层图找极大的一组增广路（迭代实现，避免递归深度）
        ptr = [0] * n
        dead = [False] * n
        for s in range(n):
            if match_l[s] != -1 or dist[s] != 0:
                continue
            stack = [s]
            par = {s: None}  # 左点 -> (父左点, 经过的右点)
            while stack:
                u = stack[-1]
                a = cache[u]
                if a is None:
                    a = index.query(index.h[u], index.d[u], index.l[u])
                    if cached + len(a) <= cache_budget:
                        cache[u] = a
                        cached += len(a)
                la = len(a)
                pu = ptr[u]
                du = dist[u]
                moved = False
                augmented = False
                while pu < la:
                    v = a[pu]
                    pu += 1
                    w = match_r[v]
                    if w == -1:
                        # 到达空闲右点：沿 par 链改配，匹配数 +1
                        match_l[u] = v
                        match_r[v] = u
                        cur = u
                        while par[cur] is not None:
                            pl, vr = par[cur]
                            match_l[pl] = vr
                            match_r[vr] = pl
                            cur = pl
                        augmented = True
                        break
                    if (not dead[w] and dist[w] == du + 1
                            and dist[w] <= free_level and w not in par):
                        par[w] = (u, v)
                        stack.append(w)
                        moved = True
                        break
                ptr[u] = pu
                if augmented:
                    break
                if not moved:
                    dead[u] = True
                    stack.pop()
    return match_l, match_r


def _max_antichain(n, index, match_l, match_r):
    """由最大匹配还原最大反链：交替可达集 Z，保留 {左∈Z 且 右∉Z}。"""
    zl = [False] * n
    zr = [False] * n
    dq = deque()
    for u in range(n):
        if match_l[u] == -1:
            zl[u] = True
            dq.append(u)
    cache = {}
    while dq:
        u = dq.popleft()
        a = cache.get(u)
        if a is None:
            a = index.query(index.h[u], index.d[u], index.l[u])
            cache[u] = a
        mu = match_l[u]
        for v in a:
            if v == mu or zr[v]:
                continue
            zr[v] = True
            w = match_r[v]
            if w != -1 and not zl[w]:
                zl[w] = True
                dq.append(w)
    return [i for i in range(n) if zl[i] and not zr[i]]


def analyze_group(points):
    """分析一个同温同风组。

    points: [(reference, moisture, thickness, minutes), ...]
    返回 payload：
      n       组内试件数
      keep    保留名单（无冲突），按编号排序
      review  最少待复核名单，按编号排序
      chains  互不重叠、覆盖全部 n 件的冲突链（最优性证书），
              链内按 ≺ 序排列（湿度/厚度不降、实测时长严格变短）
      proof   {chains, covered, lower_bound, removals} 最优性等式
    """
    n = len(points)
    refs = [p[0] for p in points]
    hs = [float(p[1]) for p in points]
    ds = [float(p[2]) for p in points]
    ls = [float(p[3]) for p in points]

    def node(i):
        return {'reference': refs[i], 'moisture': hs[i],
                'thickness': ds[i], 'minutes': ls[i]}

    if n <= 1:
        return {'n': n, 'keep': list(refs), 'review': [],
                'chains': [[node(0)]] if n else [],
                'proof': {'chains': n, 'covered': n,
                          'lower_bound': 0, 'removals': 0}}

    hr = _rank_map(hs)
    dr = _rank_map(ds)
    lr = _rank_map(ls)
    H = [hr[x] for x in hs]
    D = [dr[x] for x in ds]
    L = [lr[x] for x in ls]

    index = _DominanceIndex(H, D, L)
    match_l, match_r = _max_matching(n, index)

    # 最小链覆盖：匹配边即链内后继
    prev = [-1] * n
    for u, v in enumerate(match_l):
        if v != -1:
            prev[v] = u
    chains_idx = []
    for i in range(n):
        if prev[i] == -1:
            chain = []
            cur = i
            while cur != -1:
                chain.append(cur)
                cur = match_l[cur]
            chains_idx.append(chain)

    keep_idx = _max_antichain(n, index, match_l, match_r)
    keep_set = set(keep_idx)

    chains = [[node(i) for i in chain] for chain in chains_idx]
    chains.sort(key=lambda c: (-len(c), c[0]['reference']))
    keep = sorted(refs[i] for i in keep_idx)
    review = sorted(refs[i] for i in range(n) if i not in keep_set)
    c = len(chains)
    return {'n': n,
            'keep': keep,
            'review': review,
            'chains': chains,
            'proof': {'chains': c, 'covered': n,
                      'lower_bound': n - c, 'removals': len(review)}}


def verify_group(points, payload):
    """独立校验分析结果，O(n log n)，不逐对枚举。失败抛 AssertionError。"""
    n = len(points)
    refs = [p[0] for p in points]
    assert len(set(refs)) == n, '样本编号必须唯一'
    idx = {r: i for i, r in enumerate(refs)}
    hs = [float(p[1]) for p in points]
    ds = [float(p[2]) for p in points]
    ls = [float(p[3]) for p in points]
    assert payload['n'] == n, '组内试件数不一致'

    # 1) 冲突链：互不相交、覆盖全部、链上相邻满足冲突序、数值与源一致
    seen = set()
    covered = 0
    for chain in payload['chains']:
        seq = []
        for nd in chain:
            r = nd['reference']
            assert r in idx, '链中出现未知编号'
            assert r not in seen, '冲突链必须互不相交'
            seen.add(r)
            covered += 1
            i = idx[r]
            assert (nd['moisture'] == hs[i] and nd['thickness'] == ds[i]
                    and nd['minutes'] == ls[i]), '链节点数值必须与源样本一致'
            seq.append(i)
        for a, b in zip(seq, seq[1:]):
            assert (hs[a] <= hs[b] and ds[a] <= ds[b]
                    and ls[a] > ls[b]), '链上相邻试件必须构成冲突'
    assert covered == n, '冲突链必须覆盖全部试件'

    # 2) 保留集无冲突：按 (H, D) 排序，树状数组维护各厚度秩的最大 L
    keep = payload['keep']
    assert len(set(keep)) == len(keep), '保留名单不得重复'
    order = sorted((idx[r] for r in keep), key=lambda i: (hs[i], ds[i]))
    dr = _rank_map(ds[i] for i in order) if order else {}
    bit = [float('-inf')] * (len(dr) + 2)

    def bit_max(j):  # max over ranks <= j（1 起）
        r = float('-inf')
        while j > 0:
            if bit[j] > r:
                r = bit[j]
            j -= j & -j
        return r

    def bit_upd(j, val):
        while j < len(bit):
            if val > bit[j]:
                bit[j] = val
            j += j & -j

    pos = 0
    while pos < len(order):
        end = pos + 1
        while (end < len(order)
               and hs[order[end]] == hs[order[pos]]
               and ds[order[end]] == ds[order[pos]]):
            end += 1
        batch = order[pos:end]
        # 同 (H, D) 的保留试件时长必须全等，否则两两冲突
        assert len({ls[i] for i in batch}) <= 1, '同湿度同厚度的保留试件时长必须一致'
        for i in batch:
            assert bit_max(dr[ds[i]] + 1) <= ls[i], '保留集中仍存在冲突'
        for i in batch:
            bit_upd(dr[ds[i]] + 1, ls[i])
        pos = end

    # 3) 名单自洽 + 最优性等式：移出数 = n − 链数 = 各链 (长度−1) 之和
    review = payload['review']
    assert len(set(review)) == len(review), '待复核名单不得重复'
    assert set(keep).isdisjoint(review), '保留与待复核不得重叠'
    assert set(keep) | set(review) == set(refs), '名单必须覆盖全部试件'
    c = len(payload['chains'])
    assert len(keep) == c, '保留数必须等于冲突链数'
    assert sum(len(chain) - 1 for chain in payload['chains']) == len(review)
    proof = payload['proof']
    assert proof['chains'] == c
    assert proof['covered'] == n
    assert proof['lower_bound'] == n - c, '下界必须等于 n − 链数'
    assert proof['removals'] == len(review) == n - c, '移出数必须等于下界'


def brute_force_min_review(points):
    """指数级暴力求最少待复核数，仅供小规模测试对照。"""
    n = len(points)
    hs = [float(p[1]) for p in points]
    ds = [float(p[2]) for p in points]
    ls = [float(p[3]) for p in points]

    def conflict(i, j):
        return ((hs[i] <= hs[j] and ds[i] <= ds[j] and ls[i] > ls[j])
                or (hs[j] <= hs[i] and ds[j] <= ds[i] and ls[j] > ls[i]))

    best = -1
    for mask in range(1 << n):
        members = [i for i in range(n) if mask >> i & 1]
        if len(members) <= best:
            continue
        ok = True
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                if conflict(members[a], members[b]):
                    ok = False
                    break
            if not ok:
                break
        if ok:
            best = len(members)
    return n - best
