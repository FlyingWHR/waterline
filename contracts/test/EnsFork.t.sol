// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {Test} from "forge-std/Test.sol";
import {Marks} from "../src/Marks.sol";
import {EnsNames} from "./EnsNames.sol";

interface IMintable {
    function mint(address to, uint256 amount) external;
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IETHRegistrar {
    function makeCommitment(string calldata, address, bytes32, address, address, uint64, bytes32)
        external
        pure
        returns (bytes32);
    function commit(bytes32) external;
    function register(string calldata, address, bytes32, address, address, uint64, address, bytes32)
        external
        returns (uint256);
    function isAvailable(string calldata) external view returns (bool);
}

interface IUniversalResolver {
    function resolve(bytes calldata name, bytes calldata data) external view returns (bytes memory, address);
}

/// Runs against a fork of live Sepolia: FOUNDRY_PROFILE unchanged, set SEPOLIA_RPC and run
/// `forge test --match-contract EnsForkTest --fork-url $SEPOLIA_RPC`.
contract EnsForkTest is Test {
    // ENSv2 names are ERC-1155 tokens; the test contract owns one here
    function onERC1155Received(address, address, uint256, uint256, bytes calldata) external pure returns (bytes4) {
        return this.onERC1155Received.selector;
    }

    function test_gpuNameResolvesFromMarksWithoutRegistration() public {
        if (block.chainid != 11155111) return; // only meaningful on a Sepolia fork
        string memory cfg = vm.readFile("./ens.sepolia.json");
        IETHRegistrar registrar = IETHRegistrar(vm.parseJsonAddress(cfg, ".ETHRegistrar"));
        IMintable usdc = IMintable(vm.parseJsonAddress(cfg, ".MockUSDC"));
        IUniversalResolver ur = IUniversalResolver(vm.parseJsonAddress(cfg, ".UniversalResolver"));
        string memory label = string.concat("wl-test-", vm.toString(block.timestamp));
        assertTrue(registrar.isAvailable(label));

        address owner = address(this);
        address api = address(0xA91);
        Marks marks = new Marks(owner, api, EnsNames.namehash(string.concat(label, ".eth")));

        // register <label>.eth with Marks as its resolver
        usdc.mint(owner, 1_000e6);
        usdc.approve(address(registrar), type(uint256).max);
        bytes32 secret = keccak256("secret");
        uint64 duration = 365 days;
        registrar.commit(registrar.makeCommitment(label, owner, secret, address(0), address(marks), duration, 0));
        vm.warp(block.timestamp + 61);
        registrar.register(label, owner, secret, address(0), address(marks), duration, address(usdc), 0);

        // a GPU name nobody registered, two levels below
        string memory name = string.concat("gpu-91c0ab12.cloud-b.", label, ".eth");
        bytes32 node = EnsNames.namehash(name);
        vm.prank(api);
        marks.record(keccak256("cloud-b"), keccak256("gpu-91c0ab12"), 2, 3, 108, bytes32(uint256(1)), keccak256("human-1"),
            keccak256("human-1@cloud-b"), 6012, 9635, keccak256("report"));

        (bytes memory out, address resolver) =
            ur.resolve(EnsNames.dnsEncode(name), abi.encodeWithSelector(Marks.text.selector, node, "waterline.status"));
        assertEq(resolver, address(marks));
        assertEq(abi.decode(out, (string)), unicode"suspect · 1 of 2 humans");

        (out,) = ur.resolve(EnsNames.dnsEncode(name), abi.encodeWithSelector(Marks.text.selector, node, "waterline.class"));
        assertEq(abi.decode(out, (string)), "A100");

        (out,) = ur.resolve(EnsNames.dnsEncode(name), abi.encodeWithSelector(Marks.text.selector, node, "waterline.tops"));
        assertEq(abi.decode(out, (string)), "601.2");
        (out,) = ur.resolve(
            EnsNames.dnsEncode(name), abi.encodeWithSelector(Marks.text.selector, node, "waterline.pct_of_spec")
        );
        assertEq(abi.decode(out, (string)), "96.35");

        // the provider name one level up carries the roll-up, also unregistered
        string memory pname = string.concat("cloud-b.", label, ".eth");
        (out,) = ur.resolve(EnsNames.dnsEncode(pname),
            abi.encodeWithSelector(Marks.text.selector, EnsNames.namehash(pname), "waterline.status"));
        assertEq(abi.decode(out, (string)), unicode"0 of 1 GPU failed · reported by 1 person");
    }
}
