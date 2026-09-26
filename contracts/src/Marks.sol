// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

/// @title Marks
/// @notice Stores renter-sourced GPU reports and serves them as ENS text records for *.waterline.eth.
///         Set as the resolver of waterline.eth, it answers every `gpu-<id>.<cloud>.waterline.eth` name
///         by wildcard (ENSIP-10), so no GPU needs its own registration. Only the Waterline API (the
///         reporter) can record reports, so a host can't publish its own pass.
contract Marks {
    uint8 public constant PASS = 1;
    uint8 public constant FAIL = 2;
    uint32 public constant HUMANS_TO_FAIL = 2;

    struct Gpu {
        uint8 cls; // last measured class: 0 unknown, 1 H100 SXM, 2 H100 PCIe, 3 A100
        uint16 cores;
        bytes32 fingerprint;
        uint32 passes;
        uint32 fails;
        uint32 humans; // distinct verified humans who reported a failure
        uint64 lastAt;
        uint32 topsX10; // latest verified INT8 TOPS x 10 (1410.5 TOPS -> 14105)
        uint16 pctBps; // latest verified TOPS as percent of the claimed model's spec x 100 (71.25% -> 7125)
    }

    address public admin;
    address public reporter;
    mapping(bytes32 node => Gpu) public gpus;
    mapping(bytes32 voteKey => bool) public voted; // keccak(node, voterId)

    event Reported(
        bytes32 indexed node,
        bytes32 voterId,
        uint8 verdict,
        uint8 cls,
        uint16 cores,
        bytes32 fingerprint,
        uint32 topsX10,
        uint16 pctBps,
        uint64 at,
        uint32 passes,
        uint32 fails,
        uint32 humans
    );
    event ReporterChanged(address reporter);

    error NotReporter();
    error NotAdmin();
    error BadVerdict();
    error AlreadyVoted();
    error UnsupportedRecord(bytes4 selector);

    constructor(address admin_, address reporter_) {
        admin = admin_;
        reporter = reporter_;
    }

    function setReporter(address reporter_) external {
        if (msg.sender != admin) revert NotAdmin();
        reporter = reporter_;
        emit ReporterChanged(reporter_);
    }

    /// @notice Record one verified report. Passes carry their own evidence; a failure needs a
    ///         voter ID (one per verified human per GPU, derived by the API from World ID).
    ///         topsX10 / pctBps: the report's verified INT8 throughput (TOPS x 10) and its percent of spec (x 100).
    function record(
        bytes32 node,
        uint8 verdict,
        uint8 cls,
        uint16 cores,
        bytes32 fingerprint,
        bytes32 voterId,
        uint32 topsX10,
        uint16 pctBps
    ) external {
        if (msg.sender != reporter) revert NotReporter();
        Gpu storage g = gpus[node];
        if (verdict == PASS) {
            g.passes += 1;
        } else if (verdict == FAIL) {
            bytes32 key = keccak256(abi.encode(node, voterId));
            if (voterId == bytes32(0) || voted[key]) revert AlreadyVoted();
            voted[key] = true;
            g.fails += 1;
            g.humans += 1;
        } else {
            revert BadVerdict();
        }
        g.cls = cls;
        g.cores = cores;
        g.fingerprint = fingerprint;
        g.lastAt = uint64(block.timestamp);
        g.topsX10 = topsX10;
        g.pctBps = pctBps;
        emit Reported(
            node, voterId, verdict, cls, cores, fingerprint, topsX10, pctBps, g.lastAt, g.passes, g.fails, g.humans
        );
    }

    // ---- reading -------------------------------------------------------------------------------

    function status(bytes32 node) public view returns (string memory) {
        Gpu storage g = gpus[node];
        if (g.humans >= HUMANS_TO_FAIL) return "failed";
        if (g.humans == 1) return unicode"suspect · 1 of 2 humans";
        if (g.passes > 0) return "pass";
        return "unknown";
    }

    function text(bytes32 node, string calldata key) public view returns (string memory) {
        Gpu storage g = gpus[node];
        bytes32 k = keccak256(bytes(key));
        if (k == keccak256("waterline.status")) return status(node);
        if (k == keccak256("waterline.class")) return _className(g.cls);
        if (k == keccak256("waterline.cores")) return _uint(g.cores);
        if (k == keccak256("waterline.passes")) return _uint(g.passes);
        if (k == keccak256("waterline.fails")) return _uint(g.fails);
        if (k == keccak256("waterline.humans")) return _uint(g.humans);
        if (k == keccak256("waterline.fingerprint")) return g.lastAt == 0 ? "" : _hex(g.fingerprint);
        if (k == keccak256("waterline.tops")) return g.lastAt == 0 ? "" : _fixed(g.topsX10, 10, 1);
        if (k == keccak256("waterline.pct_of_spec")) return g.lastAt == 0 ? "" : _fixed(g.pctBps, 100, 2);
        if (k == keccak256("description")) return "Waterline: renter-verified GPU record";
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

    function supportsInterface(bytes4 id) external pure returns (bool) {
        return id == 0x01ffc9a7 // ERC-165
            || id == 0x9061b923 // IExtendedResolver
            || id == 0x59d1d43c; // text(bytes32,string)
    }

    // ---- helpers -------------------------------------------------------------------------------

    function _className(uint8 cls) private pure returns (string memory) {
        if (cls == 1) return "H100 SXM";
        if (cls == 2) return "H100 PCIe";
        if (cls == 3) return "A100";
        return "unknown";
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
