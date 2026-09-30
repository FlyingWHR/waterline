// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {EnhancedAccessControl} from "@ensdomains/contracts-v2/access-control/EnhancedAccessControl.sol";

/// @title Marks
/// @notice Stores GPU check results and serves them as ENS text records for *.waterline.eth.
///         As the resolver of waterline.eth it answers `<cloud>.waterline.eth`, `gpu-<id>.<cloud>.waterline.eth` and
///         `<n>.gpu-<id>.<cloud>.waterline.eth` by wildcard (ENSIP-10), so no name needs registering.
///
///         The chip class comes only from heat-proof probes (core count, FP8): heat can make a GPU DEGRADED (right
///         chip, correct answers, deadline missed) but never FAIL. A pass or a degraded result carries its own
///         evidence, so one report records it. A failure carries the listing the renter rented, and it takes two
///         failure reports to mark a GPU failed. Every failure also counts against `<cloud>.waterline.eth`, so a
///         renamed chip gets a clean GPU record but not a clean provider.
///
///         Roles (ENSv2 Enhanced Access Control; resource = a name's node, root = every name):
///         REPORTER writes reports (today only the Waterline API; a grant on a provider node covers its GPUs).
///         NOTE lets a provider write `waterline.note` on its own name, beside the record and never part of it.
contract Marks is EnhancedAccessControl {
    uint8 public constant PASS = 1;
    uint8 public constant FAIL = 2;
    uint8 public constant DEGRADED = 3;
    uint32 public constant HUMANS_TO_FAIL = 2;
    uint32 public constant PASSES_TO_RECOVER = 2;
    uint256 public constant NOTE_MAX = 280;
    uint256 public constant ROLE_REPORTER = 1 << 0;
    uint256 public constant ROLE_NOTE = 1 << 4;
    uint256 public constant ROLE_REPORTER_ADMIN = ROLE_REPORTER << 128;
    uint256 public constant ROLE_NOTE_ADMIN = ROLE_NOTE << 128;
    uint256 public constant ROLE_CLASSES = 1 << 8; // names the class codes; granted to the admin at the root

    /// Display name per class code (waterline.class). Codes are permanent; a new GPU gets a new code, no redeploy.
    mapping(uint8 => string) public classNames;

    /// namehash of the parent name (waterline.eth); providers are its children, GPUs its grandchildren.
    bytes32 public immutable parent;

    struct Gpu {
        bytes32 provider; // node of <cloud>.waterline.eth
        uint8 cls; // last measured class code, named by classNames (0 unknown); core/gpu_classes.json
        uint16 cores;
        bytes32 fingerprint;
        uint32 passes;
        uint32 fails;
        uint32 humans; // failure reports ever, one per voter (kept after recovery)
        uint32 active; // failure reports counting toward status since the last recovery
        uint32 sinceFail; // passes since the last failure
        uint32 recoveries;
        uint32 degraded; // right chip, correct answers, too slow for the deadline (heat, power, sharing)
        uint8 lastVerdict;
        uint64 lastAt;
        uint32 topsX10; // latest verified INT8 TOPS x 10 (1410.5 TOPS -> 14105)
        uint16 pctBps; // latest verified TOPS as percent of the claimed model's spec x 100 (71.25% -> 7125)
        bytes32 reportHash; // keccak256 of the latest full report (canonical JSON served by the API)
        uint32 checkCount; // published checks; check n resolves as <n>.<gpu name>
    }

    /// One published check, at <n>.gpu-….<provider>.waterline.eth (node = keccak(gpuNode, keccak("<n>"))).
    struct Check {
        bytes32 gpu;
        uint8 verdict;
        uint8 cls;
        uint16 cores;
        uint32 topsX10;
        uint16 pctBps;
        uint64 at;
        bytes32 reportHash;
    }

    mapping(bytes32 => Check) public checks;

    struct Provider {
        uint32 gpus; // distinct GPUs with at least one report
        uint32 failedGpus; // GPUs whose status is failed right now
        uint32 humans; // distinct voters across its GPUs
        uint32 passes;
        uint32 fails;
        uint32 degraded;
        string note; // written by the provider (ROLE_NOTE), shown beside the record, never part of it
    }

    mapping(bytes32 node => Gpu) public gpus;
    mapping(bytes32 node => Provider) public providers;
    mapping(bytes32 voteKey => bool) public voted; // keccak(node, gpuVoter): one voice per voter per GPU, ever
    mapping(bytes32 voteKey => bool) public providerVoted; // keccak(providerNode, providerVoter)

    event Reported(
        bytes32 indexed node,
        bytes32 indexed provider,
        bytes32 gpuVoter,
        bytes32 providerVoter,
        uint8 verdict,
        uint8 cls,
        uint16 cores,
        bytes32 fingerprint,
        uint32 topsX10,
        uint16 pctBps,
        uint64 at,
        uint32 passes,
        uint32 fails,
        uint32 active,
        bytes32 reportHash
    );
    event ProviderTally(
        bytes32 indexed provider, uint32 gpus, uint32 failedGpus, uint32 humans, uint32 passes, uint32 fails, uint64 at
    );
    event NoteSet(bytes32 indexed provider, string note);
    event ClassNamed(uint8 indexed code, string name);
    error BadVerdict();
    error AlreadyVoted();
    error NoteTooLong();
    error LengthMismatch();
    error UnsupportedRecord(bytes4 selector);

    /// @param admin_ may grant and revoke the reporter and note roles.
    /// @param reporter_ the Waterline API's account; gets the reporter role for every name.
    /// @param parent_ namehash of waterline.eth.
    constructor(address admin_, address reporter_, bytes32 parent_) {
        parent = parent_;
        _grantRoles(ROOT_RESOURCE, ROLE_REPORTER_ADMIN | ROLE_NOTE_ADMIN | ROLE_CLASSES, admin_, false);
        _grantRoles(ROOT_RESOURCE, ROLE_REPORTER, reporter_, false);
    }

    /// @notice Node of `<cloudLabel>.<parent>` and of `<gpuLabel>.<cloudLabel>.<parent>` (labels are keccak256 of the label).
    function nodes(bytes32 cloudLabel, bytes32 gpuLabel) public view returns (bytes32 provider, bytes32 node) {
        provider = keccak256(abi.encodePacked(parent, cloudLabel));
        node = keccak256(abi.encodePacked(provider, gpuLabel));
    }

    /// @notice Record one verified report. Voter IDs are ignored on a pass; a failure needs both, derived by the API
    ///         from the report (one for the GPU, one for the provider).
    ///         topsX10 / pctBps: verified INT8 TOPS x 10 and percent of spec x 100.
    ///         reportHash: keccak256 of the full off-chain report.
    function record(
        bytes32 cloudLabel,
        bytes32 gpuLabel,
        uint8 verdict,
        uint8 cls,
        uint16 cores,
        bytes32 fingerprint,
        bytes32 gpuVoter,
        bytes32 providerVoter,
        uint32 topsX10,
        uint16 pctBps,
        bytes32 reportHash
    ) external {
        (bytes32 pnode, bytes32 node) = nodes(cloudLabel, gpuLabel);
        if (!hasRoles(uint256(pnode), ROLE_REPORTER, msg.sender)) _checkRoles(uint256(node), ROLE_REPORTER, msg.sender);
        _tally(node, pnode, verdict, gpuVoter, providerVoter);
        Gpu storage g = gpus[node];
        g.cls = cls;
        g.cores = cores;
        g.fingerprint = fingerprint;
        g.lastAt = uint64(block.timestamp);
        g.topsX10 = topsX10;
        g.pctBps = pctBps;
        g.reportHash = reportHash;
        _check(node, g, verdict);
        _emit(node, gpuVoter, providerVoter, verdict);
    }

    /// @dev Every published check gets its own name under the GPU: 1.gpu-…, 2.gpu-…, … (wildcard, no registration).
    function _check(bytes32 node, Gpu storage g, uint8 verdict) private {
        uint32 n = ++g.checkCount;
        bytes32 cnode = checkNode(node, n);
        checks[cnode] = Check(node, verdict, g.cls, g.cores, g.topsX10, g.pctBps, g.lastAt, g.reportHash);
    }

    /// @notice Node of check n of a GPU: namehash("<n>." + the GPU's name).
    function checkNode(bytes32 gpuNode, uint32 n) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(gpuNode, keccak256(bytes(_uint(n)))));
    }

    /// @notice A provider's note on its own name. Needs ROLE_NOTE on that name; changes no count or status.
    function setNote(bytes32 providerNode, string calldata note) external {
        _checkRoles(uint256(providerNode), ROLE_NOTE, msg.sender);
        if (bytes(note).length > NOTE_MAX) revert NoteTooLong();
        providers[providerNode].note = note;
        emit NoteSet(providerNode, note);
    }

    /// @notice Name class codes (e.g. 5 -> "H200"). Changes labels only.
    function setClassNames(uint8[] calldata codes, string[] calldata names) external {
        _checkRoles(ROOT_RESOURCE, ROLE_CLASSES, msg.sender);
        if (codes.length != names.length) revert LengthMismatch();
        for (uint256 i; i < codes.length; i++) {
            classNames[codes[i]] = names[i];
            emit ClassNamed(codes[i], names[i]);
        }
    }

    /// @dev Counts, votes, recovery and the provider roll-up for one report.
    function _tally(bytes32 node, bytes32 pnode, uint8 verdict, bytes32 gpuVoter, bytes32 providerVoter) private {
        Gpu storage g = gpus[node];
        Provider storage p = providers[pnode];
        if (g.lastAt == 0) {
            g.provider = pnode;
            p.gpus += 1;
        }
        bool wasFailed = g.active >= HUMANS_TO_FAIL;
        if (verdict == PASS) {
            g.passes += 1;
            p.passes += 1;
            g.sinceFail += 1;
            if (g.active > 0 && g.sinceFail >= PASSES_TO_RECOVER) {
                g.active = 0;
                g.recoveries += 1;
            }
        } else if (verdict == DEGRADED) {
            g.degraded += 1; // no recovery credit, no vote
            p.degraded += 1;
        } else if (verdict == FAIL) {
            _vote(node, pnode, gpuVoter, providerVoter);
            g.fails += 1;
            p.fails += 1;
            g.humans += 1;
            g.active += 1;
            g.sinceFail = 0;
        } else {
            revert BadVerdict();
        }
        g.lastVerdict = verdict;
        bool isFailed = g.active >= HUMANS_TO_FAIL;
        if (isFailed && !wasFailed) p.failedGpus += 1;
        if (wasFailed && !isFailed) p.failedGpus -= 1;
    }

    function _vote(bytes32 node, bytes32 pnode, bytes32 gpuVoter, bytes32 providerVoter) private {
        bytes32 key = keccak256(abi.encode(node, gpuVoter));
        if (gpuVoter == bytes32(0) || providerVoter == bytes32(0) || voted[key]) revert AlreadyVoted();
        voted[key] = true;
        bytes32 pkey = keccak256(abi.encode(pnode, providerVoter));
        if (!providerVoted[pkey]) {
            providerVoted[pkey] = true;
            providers[pnode].humans += 1;
        }
    }

    function _emit(bytes32 node, bytes32 gpuVoter, bytes32 providerVoter, uint8 verdict) private {
        Gpu storage g = gpus[node];
        emit Reported(
            node,
            g.provider,
            gpuVoter,
            providerVoter,
            verdict,
            g.cls,
            g.cores,
            g.fingerprint,
            g.topsX10,
            g.pctBps,
            g.lastAt,
            g.passes,
            g.fails,
            g.active,
            g.reportHash
        );
        Provider storage p = providers[g.provider];
        emit ProviderTally(g.provider, p.gpus, p.failedGpus, p.humans, p.passes, p.fails, g.lastAt);
    }

    // ---- reading -------------------------------------------------------------------------------

    function status(bytes32 node) public view returns (string memory) {
        Gpu storage g = gpus[node];
        if (g.active >= HUMANS_TO_FAIL) return "failed";
        if (g.active == 1) return unicode"suspect · 1 of 2 reports";
        if (g.lastVerdict == DEGRADED) return "degraded";
        if (g.recoveries > 0) return "recovered";
        if (g.passes > 0) return "pass";
        return "unknown";
    }

    /// @notice Descriptive, not a verdict: a provider is judged GPU by GPU.
    function providerStatus(bytes32 pnode) public view returns (string memory) {
        Provider storage p = providers[pnode];
        if (p.gpus == 0) return "unknown";
        return string.concat(
            _uint(p.failedGpus), " of ", _uint(p.gpus), p.gpus == 1 ? " GPU failed" : " GPUs failed", unicode" · ",
            _uint(p.humans), p.humans == 1 ? " failure report" : " failure reports"
        );
    }

    function text(bytes32 node, string calldata key) public view returns (string memory) {
        bytes32 k = keccak256(bytes(key));
        if (k == keccak256("description")) return "Waterline: renter-verified GPU record";
        Provider storage p = providers[node];
        if (p.gpus > 0 || bytes(p.note).length > 0) return _providerText(p, node, k);
        Check storage c = checks[node];
        if (c.at != 0) return _checkText(c, k);
        Gpu storage g = gpus[node];
        if (k == keccak256("waterline.checks")) return _uint(g.checkCount);
        if (k == keccak256("waterline.status")) return status(node);
        if (k == keccak256("waterline.class")) return _className(g.cls);
        if (k == keccak256("waterline.cores")) return _uint(g.cores);
        if (k == keccak256("waterline.passes")) return _uint(g.passes);
        if (k == keccak256("waterline.fails")) return _uint(g.fails);
        if (k == keccak256("waterline.humans")) return _uint(g.humans);
        if (k == keccak256("waterline.recoveries")) return _uint(g.recoveries);
        if (k == keccak256("waterline.degraded")) return _uint(g.degraded);
        if (k == keccak256("waterline.fingerprint")) return g.lastAt == 0 ? "" : _hex(g.fingerprint);
        if (k == keccak256("waterline.tops")) return g.lastAt == 0 ? "" : _fixed(g.topsX10, 10, 1);
        if (k == keccak256("waterline.pct_of_spec")) return g.lastAt == 0 ? "" : _fixed(g.pctBps, 100, 2);
        if (k == keccak256("waterline.report")) return g.lastAt == 0 ? "" : _hex(g.reportHash);
        return "";
    }

    function _checkText(Check storage c, bytes32 k) private view returns (string memory) {
        if (k == keccak256("waterline.verdict")) return c.verdict == PASS ? "pass" : c.verdict == FAIL ? "fail" : "degraded";
        if (k == keccak256("waterline.class")) return _className(c.cls);
        if (k == keccak256("waterline.cores")) return _uint(c.cores);
        if (k == keccak256("waterline.tops")) return _fixed(c.topsX10, 10, 1);
        if (k == keccak256("waterline.pct_of_spec")) return _fixed(c.pctBps, 100, 2);
        if (k == keccak256("waterline.at")) return _uint(c.at);
        if (k == keccak256("waterline.report")) return _hex(c.reportHash);
        if (k == keccak256("waterline.gpu")) return _hex(c.gpu);
        return "";
    }

    function _providerText(Provider storage p, bytes32 node, bytes32 k) private view returns (string memory) {
        if (k == keccak256("waterline.status")) return providerStatus(node);
        if (k == keccak256("waterline.gpus")) return _uint(p.gpus);
        if (k == keccak256("waterline.failed_gpus")) return _uint(p.failedGpus);
        if (k == keccak256("waterline.humans")) return _uint(p.humans);
        if (k == keccak256("waterline.passes")) return _uint(p.passes);
        if (k == keccak256("waterline.fails")) return _uint(p.fails);
        if (k == keccak256("waterline.degraded")) return _uint(p.degraded);
        if (k == keccak256("waterline.note")) return p.note;
        return "";
    }

    // ---- ENS (ENSIP-10 wildcard resolver) --------------------------------------------------------

    /// @dev `data` is an ABI-encoded resolver call whose node is the namehash of the full name.
    function resolve(bytes calldata, bytes calldata data) external view returns (bytes memory) {
        bytes4 selector = bytes4(data[:4]);
        if (selector == this.text.selector) {
            (bytes32 node, string memory key) = abi.decode(data[4:], (bytes32, string));
            return abi.encode(this.text(node, key));
        }
        if (selector == 0x3b3b57de) return abi.encode(address(0)); // addr(bytes32)
        if (selector == 0xf1cb7e06) return abi.encode(bytes("")); // addr(bytes32,uint256)
        revert UnsupportedRecord(selector);
    }

    function supportsInterface(bytes4 id) public view override returns (bool) {
        return id == 0x9061b923 // IExtendedResolver
            || id == 0x59d1d43c // text(bytes32,string)
            || super.supportsInterface(id); // ERC-165 + IEnhancedAccessControl
    }

    // ---- helpers -------------------------------------------------------------------------------

    function _className(uint8 cls) private view returns (string memory) {
        string memory n = classNames[cls];
        return bytes(n).length == 0 ? "unknown" : n;
    }

    function _uint(uint256 v) private pure returns (string memory) {
        if (v == 0) return "0";
        bytes memory b = new bytes(78);
        uint256 i = b.length;
        while (v > 0) {
            b[--i] = bytes1(uint8(48 + v % 10));
            v /= 10;
        }
        bytes memory out = new bytes(b.length - i);
        for (uint256 j; j < out.length; ++j) out[j] = b[i + j];
        return string(out);
    }

    /// @dev v / scale with `decimals` digits after the point: _fixed(14105, 10, 1) = "1410.5", _fixed(7105, 100, 2) = "71.05".
    function _fixed(uint256 v, uint256 scale, uint256 decimals) private pure returns (string memory) {
        bytes memory frac = bytes(_uint(scale + v % scale)); // leading "1" keeps the zero padding
        bytes memory out = new bytes(decimals);
        for (uint256 i; i < decimals; ++i) out[i] = frac[i + 1];
        return string.concat(_uint(v / scale), ".", string(out));
    }

    function _hex(bytes32 v) private pure returns (string memory) {
        bytes16 digits = "0123456789abcdef";
        bytes memory out = new bytes(66);
        out[0] = "0";
        out[1] = "x";
        for (uint256 i; i < 32; ++i) {
            out[2 + i * 2] = digits[uint8(v[i]) >> 4];
            out[3 + i * 2] = digits[uint8(v[i]) & 0x0f];
        }
        return string(out);
    }
}
