// f12 事件监听断点，断出脚本。访问对应的页面。在即将跳转前暂停控制台注入hook js，释放脚本断点运行。到debugger点，查看堆栈跳转执行函数进行分析
(function() {
    function reload(event) {
        debugger;
    }
    window.addEventListener('beforeunload', reload);
})();

/*
用本地文件替换线上文件：
file:///F:/Edge%E6%9B%BF%E6%8D%A2%E5%86%85%E5%AE%B9/static.pddpic.com/assets/js/react_pdd_eb7c98d1405ae6ba8e5a.js

*/

// 6751行 替换内容如下
var u = function() {
    try {
        history.replaceState(n, "", e)
    } catch (e) {}
    // setTimeout((function() {
    //     window && window.location ? window.location = e : location.href = e,
    //     "function" == typeof t && t()
    // }
    // ), 0)
};